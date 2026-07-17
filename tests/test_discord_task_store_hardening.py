import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_serialization import encode_document
from study_discord_agent.discord_task_store import (
    DiscordTaskStore,
    TaskStoreCorruptionError,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
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
        failure = DiscordTaskFailure(
            category=DiscordTaskFailureCategory.INTERNAL,
            summary="The task failed safely.",
            retry_mode=DiscordTaskRetryMode.NONE,
        )
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


@pytest.mark.parametrize("field", ["revision", "attempt"])
@pytest.mark.parametrize("invalid", [True, 1.5])
def test_record_rejects_non_integer_revision_and_attempt(field: str, invalid: object) -> None:
    with pytest.raises(ValueError):
        replace(_record(), **{field: invalid})


@pytest.mark.parametrize("expected_revision", [True, 0.0, 1.5])
def test_compare_and_set_rejects_non_integer_revisions(
    tmp_path: Path, expected_revision: object
) -> None:
    store = DiscordTaskStore(tmp_path / "discord-tasks.json")
    store.create(_record())

    with pytest.raises(ValueError, match="expected_revision"):
        store.compare_and_set(
            _record().task_id, cast(int, expected_revision), lambda record: record
        )


def test_create_rejects_records_with_continuation_links(tmp_path: Path) -> None:
    store = DiscordTaskStore(tmp_path / "discord-tasks.json")
    linked = replace(
        _record(state=DiscordTaskState.COMPLETED),
        continued_to_task_id="123e4567-e89b-12d3-a456-426614174001",
    )

    with pytest.raises(ValueError, match="link_child"):
        store.create(linked)


@pytest.mark.parametrize("records", ["missing_reciprocal", "dangling", "scope_mismatch", "cycle"])
def test_load_rejects_invalid_continuation_graphs(tmp_path: Path, records: str) -> None:
    parent = _record(state=DiscordTaskState.COMPLETED)
    child = _record("123e4567-e89b-12d3-a456-426614174001", DiscordTaskState.COMPLETED)
    if records == "missing_reciprocal":
        payload = {
            parent.task_id: replace(parent, continued_to_task_id=child.task_id),
            child.task_id: child,
        }
    elif records == "dangling":
        payload = {
            parent.task_id: replace(
                parent, continued_to_task_id="123e4567-e89b-12d3-a456-426614174099"
            )
        }
    elif records == "scope_mismatch":
        payload = {
            parent.task_id: replace(parent, continued_to_task_id=child.task_id),
            child.task_id: replace(child, continued_from_task_id=parent.task_id, owner_id=99),
        }
    else:
        payload = {
            parent.task_id: replace(
                parent, continued_from_task_id=child.task_id, continued_to_task_id=child.task_id
            ),
            child.task_id: replace(
                child, continued_from_task_id=parent.task_id, continued_to_task_id=parent.task_id
            ),
        }
    path = tmp_path / "discord-tasks.json"
    path.write_text(encode_document(payload))

    with pytest.raises(TaskStoreCorruptionError, match="schema"):
        DiscordTaskStore(path)


@pytest.mark.parametrize(
    ("make_record", "message"),
    [
        (lambda: replace(_record(), state=DiscordTaskState.FAILED), "failure"),
        (lambda: replace(_record(), state=DiscordTaskState.TIMED_OUT), "failure"),
        (lambda: replace(_record(), state=DiscordTaskState.DELIVERY_FAILED), "failure"),
        (
            lambda: replace(_record(state=DiscordTaskState.COMPLETED), failure=DELIVERY_FAILURE),
            "cannot carry failure",
        ),
        (
            lambda: replace(_record(state=DiscordTaskState.STOPPED), failure=DELIVERY_FAILURE),
            "cannot carry failure",
        ),
        (
            lambda: replace(
                _record(state=DiscordTaskState.DELIVERY_FAILED),
                failure=DiscordTaskFailure(
                    category=DiscordTaskFailureCategory.INTERNAL,
                    summary="Internal failure.",
                    retry_mode=DiscordTaskRetryMode.NONE,
                ),
            ),
            "delivery failure",
        ),
    ],
)
def test_record_enforces_state_and_failure_invariants(
    make_record: Callable[[], DiscordTaskRecord], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_record()


def test_link_child_refreshes_an_old_parent_before_retention(tmp_path: Path) -> None:
    clock = [NOW - timedelta(days=31)]
    store = DiscordTaskStore(tmp_path / "discord-tasks.json", clock=lambda: clock[0])
    parent = _record(state=DiscordTaskState.COMPLETED, updated_at=clock[0])
    child = replace(
        _record("123e4567-e89b-12d3-a456-426614174001"),
        continued_from_task_id=parent.task_id,
    )
    store.create(parent)
    clock[0] = NOW

    linked_parent, _ = store.link_child(parent.task_id, 0, child)

    assert linked_parent.updated_at == NOW.isoformat()
    restarted = DiscordTaskStore(tmp_path / "discord-tasks.json")
    assert restarted.get(parent.task_id).continued_to_task_id == child.task_id
    assert restarted.get(child.task_id) == child


def test_retention_orders_inactive_records_by_normalized_utc_time(tmp_path: Path) -> None:
    old = _record("123e4567-e89b-12d3-a456-426614174001", DiscordTaskState.COMPLETED)
    newer = _record("123e4567-e89b-12d3-a456-426614174002", DiscordTaskState.COMPLETED)
    old = replace(old, updated_at="2026-07-17T00:30:00+01:00")
    newer = replace(newer, updated_at="2026-07-17T00:00:00+00:00")
    records = {old.task_id: old, newer.task_id: newer}
    for index in range(498):
        record = _record(f"123e4567-e89b-12d3-a456-{index + 1000:012d}", DiscordTaskState.COMPLETED)
        records[record.task_id] = record
    path = tmp_path / "discord-tasks.json"
    path.write_text(encode_document(records))
    store = DiscordTaskStore(path, clock=lambda: NOW)

    store.create(_record("123e4567-e89b-12d3-a456-426614174003", DiscordTaskState.COMPLETED))

    assert store.get(newer.task_id) == newer
    with pytest.raises(KeyError):
        store.get(old.task_id)


def test_failed_file_permission_change_closes_descriptor_and_cleans_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import study_discord_agent.discord_task_store as store_module

    descriptor: list[int] = []
    original_mkstemp = store_module.tempfile.mkstemp

    def capture_mkstemp(
        *, suffix: str | None = None, prefix: str | None = None, dir: Path | None = None
    ) -> tuple[int, str]:
        opened, name = original_mkstemp(suffix=suffix, prefix=prefix, dir=dir)
        descriptor.append(opened)
        return opened, name

    def fail_fchmod(fd: int, mode: int) -> None:
        raise OSError("chmod unavailable")

    monkeypatch.setattr(store_module.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(store_module.os, "fchmod", fail_fchmod)
    store = DiscordTaskStore(tmp_path / "discord-tasks.json")

    with pytest.raises(OSError, match="chmod unavailable"):
        store.create(_record())

    with pytest.raises(OSError):
        os.fstat(descriptor[0])
    assert list(tmp_path.glob(".discord-tasks.json.*.tmp")) == []
