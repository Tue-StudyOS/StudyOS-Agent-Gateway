from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
    transition,
)
from study_discord_agent.discord_task_serialization import encode_document
from study_discord_agent.discord_task_store import (
    DiscordTaskStore,
    TaskRevisionConflict,
    TaskStoreDurabilityError,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
FAILURE = DiscordTaskFailure(
    category=DiscordTaskFailureCategory.INTERNAL,
    summary="The task failed safely.",
    retry_mode=DiscordTaskRetryMode.NONE,
)
DELIVERY_FAILURE = DiscordTaskFailure(
    category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
    summary="Discord could not deliver the result.",
    retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
)


def _record(
    task_id: str = "123e4567-e89b-12d3-a456-426614174000",
    state: DiscordTaskState = DiscordTaskState.STARTING,
    updated_at: datetime = NOW,
) -> DiscordTaskRecord:
    failure = None
    if state is DiscordTaskState.DELIVERY_FAILED:
        failure = DELIVERY_FAILURE
    elif state in {DiscordTaskState.FAILED, DiscordTaskState.TIMED_OUT}:
        failure = FAILURE
    return DiscordTaskRecord(
        task_id=task_id,
        revision=0,
        owner_id=1,
        guild_id=2,
        origin_channel_id=3,
        execution_channel_id=4,
        trigger_event_id=5,
        source_message_id=None,
        card_message_id=None,
        result_message_id=None,
        source_kind=DiscordTaskSourceKind.MENTION,
        source_label="Discord mention",
        created_at=updated_at.isoformat(),
        updated_at=updated_at.isoformat(),
        attempt=1,
        state=state,
        failure=failure,
    )


def test_link_child_rejects_boolean_revision_and_non_initial_attempt(tmp_path: Path) -> None:
    store = DiscordTaskStore(tmp_path / "discord-tasks.json")
    parent = _record(state=DiscordTaskState.COMPLETED)
    child = replace(
        _record("123e4567-e89b-12d3-a456-426614174001"),
        continued_from_task_id=parent.task_id,
    )
    store.create(parent)

    with pytest.raises(ValueError, match="expected_revision"):
        store.link_child(parent.task_id, False, child)
    with pytest.raises(ValueError, match="attempt"):
        store.link_child(parent.task_id, 0, replace(child, attempt=2))
    with pytest.raises(ValueError, match="attempt"):
        store.create(replace(_record("123e4567-e89b-12d3-a456-426614174002"), attempt=2))


def test_transition_and_store_enforce_exact_attempt_semantics(tmp_path: Path) -> None:
    recovering = transition(
        _record(state=DiscordTaskState.FAILED), DiscordTaskState.RECOVERING, NOW.isoformat()
    )
    delivering = transition(
        _record(state=DiscordTaskState.DELIVERY_FAILED),
        DiscordTaskState.DELIVERING,
        NOW.isoformat(),
    )
    assert recovering.attempt == 2
    assert delivering.attempt == 2

    store = DiscordTaskStore(tmp_path / "discord-tasks.json")
    store.create(_record())
    with pytest.raises(ValueError, match="attempt"):
        store.compare_and_set(
            _record().task_id,
            0,
            lambda record: replace(record, state=DiscordTaskState.RUNNING, attempt=2),
        )
    with pytest.raises(ValueError, match="attempt"):
        store.compare_and_set(
            _record().task_id,
            0,
            lambda record: replace(
                record, state=DiscordTaskState.FAILED, failure=FAILURE, attempt=2
            ),
        )

    retry_store = DiscordTaskStore(tmp_path / "retry-tasks.json")
    failed = _record("123e4567-e89b-12d3-a456-426614174003", DiscordTaskState.FAILED)
    retry_store.create(failed)
    retried = retry_store.compare_and_set(
        failed.task_id,
        0,
        lambda record: replace(record, state=DiscordTaskState.RECOVERING, attempt=2),
    )
    assert retried.attempt == 2


@pytest.mark.parametrize(
    "record",
    [
        lambda: replace(_record(state=DiscordTaskState.FAILED), failure=DELIVERY_FAILURE),
        lambda: replace(_record(), failure=DELIVERY_FAILURE),
        lambda: replace(
            _record(state=DiscordTaskState.FAILED),
            failure=DiscordTaskFailure(
                category=DiscordTaskFailureCategory.INTERNAL,
                summary="The task failed safely.",
                retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
            ),
        ),
    ],
)
def test_delivery_retry_metadata_is_exclusive_to_delivery_failed(
    record: Callable[[], DiscordTaskRecord],
) -> None:
    with pytest.raises(ValueError, match="delivery"):
        record()


def test_retention_preserves_an_active_task_ancestry_component(tmp_path: Path) -> None:
    parent = replace(
        _record(state=DiscordTaskState.COMPLETED, updated_at=NOW - timedelta(days=31)),
        continued_to_task_id="123e4567-e89b-12d3-a456-426614174001",
    )
    child = replace(
        _record("123e4567-e89b-12d3-a456-426614174001", DiscordTaskState.RUNNING),
        continued_from_task_id=parent.task_id,
    )
    path = tmp_path / "discord-tasks.json"
    path.write_text(encode_document({parent.task_id: parent, child.task_id: child}))
    store = DiscordTaskStore(path, clock=lambda: NOW)

    store.create(_record("123e4567-e89b-12d3-a456-426614174002", DiscordTaskState.COMPLETED))

    assert store.get(parent.task_id).continued_to_task_id == child.task_id
    assert store.get(child.task_id).continued_from_task_id == parent.task_id


def test_retention_prunes_an_inactive_continuation_component_as_a_unit(tmp_path: Path) -> None:
    parent = replace(
        _record(state=DiscordTaskState.COMPLETED, updated_at=NOW - timedelta(minutes=1)),
        continued_to_task_id="123e4567-e89b-12d3-a456-426614174001",
    )
    child = replace(
        _record(
            "123e4567-e89b-12d3-a456-426614174001",
            DiscordTaskState.COMPLETED,
            NOW - timedelta(minutes=2),
        ),
        continued_from_task_id=parent.task_id,
    )
    records = {parent.task_id: parent, child.task_id: child}
    for index in range(499):
        record = _record(
            f"123e4567-e89b-12d3-a456-{index + 1000:012d}",
            DiscordTaskState.COMPLETED,
        )
        records[record.task_id] = record
    path = tmp_path / "discord-tasks.json"
    path.write_text(encode_document(records))
    store = DiscordTaskStore(path, clock=lambda: NOW)

    store.create(_record("123e4567-e89b-12d3-a456-426614174002", DiscordTaskState.COMPLETED))

    with pytest.raises(KeyError):
        store.get(parent.task_id)
    with pytest.raises(KeyError):
        store.get(child.task_id)


def test_post_replace_directory_fsync_updates_memory_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import study_discord_agent.discord_task_persistence as persistence

    store = DiscordTaskStore(tmp_path / "discord-tasks.json")
    store.create(_record())

    def fail_directory_fsync(path: Path) -> None:
        raise OSError("directory sync unavailable")

    monkeypatch.setattr(persistence, "_fsync_directory", fail_directory_fsync)
    with pytest.raises(TaskStoreDurabilityError):
        store.compare_and_set(
            _record().task_id,
            0,
            lambda record: transition(record, DiscordTaskState.RUNNING, NOW.isoformat()),
        )

    assert store.get(_record().task_id).revision == 1
    with pytest.raises(TaskRevisionConflict):
        store.compare_and_set(_record().task_id, 0, lambda record: record)


def test_reconcile_treats_legacy_delivering_result_as_completed(tmp_path: Path) -> None:
    store = DiscordTaskStore(tmp_path / "discord-tasks.json", clock=lambda: NOW)
    delivered = replace(
        _record(state=DiscordTaskState.DELIVERING),
        result_message_id=42,
    )
    store.create(delivered)

    changed = store.reconcile_startup(NOW)

    assert len(changed) == 1
    completed = changed[0]
    assert completed.state is DiscordTaskState.COMPLETED
    assert completed.result_message_id == 42
    assert completed.failure is None
