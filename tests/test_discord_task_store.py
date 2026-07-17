import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
    transition,
)
from study_discord_agent.discord_task_serialization import encode_document
from study_discord_agent.discord_task_store import (
    DiscordTaskStore,
    TaskAlreadyExists,
    TaskRevisionConflict,
    TaskStoreCorruptionError,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _record(
    task_id: str = "123e4567-e89b-12d3-a456-426614174000",
    state: DiscordTaskState = DiscordTaskState.STARTING,
    updated_at: datetime = NOW,
    **changes: Any,
) -> DiscordTaskRecord:
    failure = None
    if state is DiscordTaskState.DELIVERY_FAILED:
        failure = DiscordTaskFailure(
            category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
            summary="Discord could not deliver the result.",
            retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
        )
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
        created_at=NOW.isoformat(),
        updated_at=updated_at.isoformat(),
        attempt=1,
        state=state,
        failure=failure,
        **changes,
    )


def _store(tmp_path: Path, now: datetime = NOW) -> DiscordTaskStore:
    return DiscordTaskStore(tmp_path / "discord-tasks.json", clock=lambda: now)


def test_create_round_trips_only_explicit_schema_with_owner_only_permissions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _record(
        intent=DiscordTaskIntent.SECURITY_REVIEW,
        source_reference_id="a" * 32,
        repository_commit_sha="b" * 40,
    )
    store.create(record)

    payload = json.loads((tmp_path / "discord-tasks.json").read_text())
    assert set(payload) == {"version", "tasks"}
    assert payload["version"] == 2
    persisted = payload["tasks"][record.task_id]
    assert set(persisted) == {
        "task_id",
        "revision",
        "owner_id",
        "guild_id",
        "origin_channel_id",
        "execution_channel_id",
        "trigger_event_id",
        "source_message_id",
        "card_message_id",
        "result_message_id",
        "source_kind",
        "source_label",
        "created_at",
        "updated_at",
        "attempt",
        "state",
        "failure",
        "interruption_cause",
        "continued_from_task_id",
        "continued_to_task_id",
        "intent",
        "source_reference_id",
        "repository_commit_sha",
    }
    assert persisted["intent"] == "security_review"
    assert persisted["source_reference_id"] == "a" * 32
    assert persisted["repository_commit_sha"] == "b" * 40
    assert os.stat(tmp_path / "discord-tasks.json").st_mode & 0o777 == 0o600
    assert DiscordTaskStore(tmp_path / "discord-tasks.json").get(record.task_id) == record


def test_loads_v1_records_with_safe_general_task_bridge_defaults(tmp_path: Path) -> None:
    path = tmp_path / "discord-tasks.json"
    payload = json.loads(encode_document({_record().task_id: _record()}))
    payload["version"] = 1
    persisted = payload["tasks"][_record().task_id]
    for field in ("intent", "source_reference_id", "repository_commit_sha"):
        persisted.pop(field, None)
    path.write_text(json.dumps(payload))

    loaded = DiscordTaskStore(path).get(_record().task_id)

    assert loaded.intent is DiscordTaskIntent.GENERAL
    assert loaded.source_reference_id is None
    assert loaded.repository_commit_sha is None


def test_hyphenated_task_id_is_retrievable_through_component_hex_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _record()
    store.create(record)

    assert store.get("123e4567e89b12d3a456426614174000") == record
    updated = store.compare_and_set(
        "123e4567e89b12d3a456426614174000",
        record.revision,
        lambda current: transition(current, DiscordTaskState.RUNNING, NOW.isoformat()),
    )

    assert updated.state is DiscordTaskState.RUNNING


def test_load_rejects_uuid_alias_duplicates(tmp_path: Path) -> None:
    first = _record()
    alias = _record(task_id="123e4567e89b12d3a456426614174000")
    path = tmp_path / "discord-tasks.json"
    path.write_text(encode_document({first.task_id: first, alias.task_id: alias}))

    with pytest.raises(TaskStoreCorruptionError):
        DiscordTaskStore(path)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 3, "tasks": {}},
        {"version": True, "tasks": {}},
        {"version": 1, "tasks": [], "extra": True},
        {"version": 1, "tasks": {"not-a-uuid": {}}},
    ],
)
def test_load_rejects_wrong_or_corrupt_schema(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "discord-tasks.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(TaskStoreCorruptionError):
        DiscordTaskStore(path)


def test_load_rejects_unknown_metadata_that_could_hold_sensitive_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_record())
    path = tmp_path / "discord-tasks.json"
    payload = json.loads(path.read_text())
    payload["tasks"][_record().task_id]["prompt"] = "do not persist this"
    path.write_text(json.dumps(payload))

    with pytest.raises(TaskStoreCorruptionError):
        DiscordTaskStore(path)


def test_compare_and_set_increments_revision_and_rejects_stale_writers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_record())

    updated = store.compare_and_set(
        _record().task_id,
        0,
        lambda record: transition(record, DiscordTaskState.RUNNING, NOW.isoformat()),
    )

    assert updated.revision == 1
    with pytest.raises(TaskRevisionConflict):
        store.compare_and_set(_record().task_id, 0, lambda record: record)


def test_failed_atomic_write_does_not_publish_memory_or_disk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.create(_record())
    original = (tmp_path / "discord-tasks.json").read_bytes()
    monkeypatch.setattr("study_discord_agent.discord_task_persistence.os.replace", _raise_replace)

    with pytest.raises(OSError, match="disk unavailable"):
        store.compare_and_set(
            _record().task_id,
            0,
            lambda record: transition(record, DiscordTaskState.RUNNING, NOW.isoformat()),
        )

    assert store.get(_record().task_id).state is DiscordTaskState.STARTING
    assert (tmp_path / "discord-tasks.json").read_bytes() == original


def test_link_child_persists_both_records_or_neither(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    parent = _record(state=DiscordTaskState.COMPLETED)
    child = _record(
        task_id="123e4567-e89b-12d3-a456-426614174001",
        state=DiscordTaskState.STARTING,
        continued_from_task_id=parent.task_id,
    )
    store.create(parent)
    monkeypatch.setattr("study_discord_agent.discord_task_persistence.os.replace", _raise_replace)

    with pytest.raises(OSError):
        store.link_child(parent.task_id, 0, child)

    assert store.get(parent.task_id) == parent
    with pytest.raises(KeyError):
        store.get(child.task_id)


def test_link_child_requires_latest_completed_parent_and_links_both_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = _record(state=DiscordTaskState.COMPLETED)
    child = _record(
        task_id="123e4567-e89b-12d3-a456-426614174001",
        continued_from_task_id=parent.task_id,
    )
    store.create(parent)

    with pytest.raises(ValueError, match="linked starting"):
        store.link_child(
            parent.task_id,
            0,
            replace(child, intent=DiscordTaskIntent.REVIEW),
        )

    linked_parent, linked_child = store.link_child(parent.task_id, 0, child)

    assert linked_parent.continued_to_task_id == child.task_id
    assert linked_parent.revision == 1
    assert linked_child == child
    with pytest.raises((TaskAlreadyExists, TaskRevisionConflict, ValueError)):
        store.link_child(parent.task_id, 1, child)


def test_startup_reconciliation_interrupts_work_and_disables_delivery_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = [
        _record(task_id=f"123e4567-e89b-12d3-a456-42661417400{index}", state=state)
        for index, state in enumerate(
            (
                DiscordTaskState.RECOVERING,
                DiscordTaskState.STARTING,
                DiscordTaskState.RUNNING,
                DiscordTaskState.STOPPING,
                DiscordTaskState.DELIVERING,
                DiscordTaskState.DELIVERY_FAILED,
            )
        )
    ]
    delivery_failure = DiscordTaskFailure(
        category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
        summary="Discord could not deliver the result.",
        retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
    )
    records[-1] = replace(records[-1], failure=delivery_failure)
    for index, record in enumerate(records):
        store.create(replace(record, execution_channel_id=index + 10))

    changed = {record.task_id: record for record in store.reconcile_startup(NOW)}

    assert {record.state for record in changed.values()} == {
        DiscordTaskState.INTERRUPTED,
        DiscordTaskState.DELIVERY_FAILED,
    }
    delivery_failure_after_restart = changed[records[4].task_id].failure
    retry_failure_after_restart = changed[records[5].task_id].failure
    assert delivery_failure_after_restart is not None
    assert retry_failure_after_restart is not None
    assert delivery_failure_after_restart.retry_mode is DiscordTaskRetryMode.NONE
    assert retry_failure_after_restart.retry_mode is DiscordTaskRetryMode.NONE


def test_retention_prunes_old_and_excess_inactive_tasks_but_keeps_active(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = _record(
        task_id="123e4567-e89b-12d3-a456-426614174001",
        state=DiscordTaskState.COMPLETED,
        updated_at=NOW - timedelta(days=31),
    )
    active = _record(task_id="123e4567-e89b-12d3-a456-426614174002", state=DiscordTaskState.RUNNING)
    store.create(old)
    store.create(active)
    for index in range(501):
        store.create(
            _record(
                task_id=f"123e4567-e89b-12d3-a456-{index + 1000:012d}",
                state=DiscordTaskState.COMPLETED,
                updated_at=NOW - timedelta(minutes=index),
            )
        )

    assert (
        len([record for record in store.records() if record.state is DiscordTaskState.COMPLETED])
        == 500
    )
    assert store.get(active.task_id) == active
    with pytest.raises(KeyError):
        store.get(old.task_id)


def test_compare_and_set_cannot_link_a_child_or_change_its_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = _record(state=DiscordTaskState.COMPLETED)
    child = replace(
        _record(
            task_id="123e4567-e89b-12d3-a456-426614174001",
            continued_from_task_id=parent.task_id,
        ),
        owner_id=99,
    )
    store.create(parent)

    with pytest.raises(ValueError, match="linked starting"):
        store.link_child(parent.task_id, 0, child)
    with pytest.raises(ValueError, match="identity"):
        store.compare_and_set(
            parent.task_id,
            0,
            lambda record: replace(record, continued_to_task_id=child.task_id),
        )


def test_compare_and_set_cannot_change_persisted_task_bridge_context(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _record(
        intent=DiscordTaskIntent.REVIEW,
        source_reference_id="a" * 32,
        repository_commit_sha="b" * 40,
    )
    store.create(record)

    changes = (
        {"intent": DiscordTaskIntent.IMPLEMENTATION},
        {"source_reference_id": "c" * 32},
        {"repository_commit_sha": "d" * 40},
    )
    for change in changes:
        with pytest.raises(ValueError, match="identity"):
            store.compare_and_set(
                record.task_id,
                record.revision,
                lambda current, change=change: replace(current, **change),
            )


def _raise_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
    raise OSError("disk unavailable")
