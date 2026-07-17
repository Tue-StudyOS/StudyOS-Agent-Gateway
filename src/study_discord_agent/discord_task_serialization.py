import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, cast

from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)

SCHEMA_VERSION: Final = 1
_RECORD_KEYS: Final = frozenset(
    {
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
    }
)


def encode_document(tasks: Mapping[str, DiscordTaskRecord]) -> str:
    document = {
        "version": SCHEMA_VERSION,
        "tasks": {key: _encode_record(value) for key, value in tasks.items()},
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def decode_document(serialized: str) -> dict[str, DiscordTaskRecord]:
    try:
        payload: object = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ValueError("document is not JSON") from error
    document = _object(payload, "document")
    if (
        set(document) != {"version", "tasks"}
        or type(document["version"]) is not int
        or document["version"] != SCHEMA_VERSION
    ):
        raise ValueError("unsupported document")
    tasks = _object(document["tasks"], "tasks")
    result: dict[str, DiscordTaskRecord] = {}
    for task_id, raw_record in tasks.items():
        record = _decode_record(raw_record)
        if record.task_id != task_id:
            raise ValueError("task key does not match task id")
        result[task_id] = record
    return result


def _encode_record(record: DiscordTaskRecord) -> dict[str, object]:
    failure = None
    if record.failure is not None:
        failure = {
            "category": record.failure.category.value,
            "summary": record.failure.summary,
            "retry_mode": record.failure.retry_mode.value,
        }
    return {
        "task_id": record.task_id,
        "revision": record.revision,
        "owner_id": record.owner_id,
        "guild_id": record.guild_id,
        "origin_channel_id": record.origin_channel_id,
        "execution_channel_id": record.execution_channel_id,
        "trigger_event_id": record.trigger_event_id,
        "source_message_id": record.source_message_id,
        "card_message_id": record.card_message_id,
        "result_message_id": record.result_message_id,
        "source_kind": record.source_kind.value,
        "source_label": record.source_label,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "attempt": record.attempt,
        "state": record.state.value,
        "failure": failure,
        "interruption_cause": (
            record.interruption_cause.value if record.interruption_cause is not None else None
        ),
        "continued_from_task_id": record.continued_from_task_id,
        "continued_to_task_id": record.continued_to_task_id,
    }


def _decode_record(payload: object) -> DiscordTaskRecord:
    data = _object(payload, "task")
    if frozenset(data) != _RECORD_KEYS:
        raise ValueError("task keys do not match schema")
    failure_value = data["failure"]
    failure = None if failure_value is None else _decode_failure(failure_value)
    return DiscordTaskRecord(
        task_id=_string(data["task_id"], "task_id"),
        revision=_integer(data["revision"], "revision"),
        owner_id=_integer(data["owner_id"], "owner_id"),
        guild_id=_integer(data["guild_id"], "guild_id"),
        origin_channel_id=_integer(data["origin_channel_id"], "origin_channel_id"),
        execution_channel_id=_integer(data["execution_channel_id"], "execution_channel_id"),
        trigger_event_id=_integer(data["trigger_event_id"], "trigger_event_id"),
        source_message_id=_optional_integer(data["source_message_id"], "source_message_id"),
        card_message_id=_optional_integer(data["card_message_id"], "card_message_id"),
        result_message_id=_optional_integer(data["result_message_id"], "result_message_id"),
        source_kind=DiscordTaskSourceKind(_string(data["source_kind"], "source_kind")),
        source_label=_string(data["source_label"], "source_label"),
        created_at=_timestamp(data["created_at"], "created_at"),
        updated_at=_timestamp(data["updated_at"], "updated_at"),
        attempt=_integer(data["attempt"], "attempt"),
        state=DiscordTaskState(_string(data["state"], "state")),
        failure=failure,
        interruption_cause=_optional_cause(data["interruption_cause"]),
        continued_from_task_id=_optional_string(
            data["continued_from_task_id"], "continued_from_task_id"
        ),
        continued_to_task_id=_optional_string(data["continued_to_task_id"], "continued_to_task_id"),
    )


def _decode_failure(payload: object) -> DiscordTaskFailure:
    data = _object(payload, "failure")
    if set(data) != {"category", "summary", "retry_mode"}:
        raise ValueError("failure keys do not match schema")
    return DiscordTaskFailure(
        category=DiscordTaskFailureCategory(_string(data["category"], "category")),
        summary=_string(data["summary"], "summary"),
        retry_mode=DiscordTaskRetryMode(_string(data["retry_mode"], "retry_mode")),
    )


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    raw_object = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw_object.items():
        if not isinstance(key, str):
            raise ValueError(f"{name} keys must be strings")
        result[key] = item
    return result


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_integer(value: object, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    return None if value is None else _string(value, name)


def _optional_cause(value: object) -> DiscordTaskInterruptionCause | None:
    return (
        None
        if value is None
        else DiscordTaskInterruptionCause(_string(value, "interruption_cause"))
    )


def _timestamp(value: object, name: str) -> str:
    timestamp = _string(value, name)
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must have a timezone")
    parsed.astimezone(UTC)
    return timestamp
