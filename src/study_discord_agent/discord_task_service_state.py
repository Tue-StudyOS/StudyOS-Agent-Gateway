from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from study_discord_agent.discord_task_model import (
    DiscordTaskRecord,
    DiscordTaskState,
)
from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_store import DiscordTaskStore


class BoundedClaims:
    def __init__(self, maximum: int) -> None:
        if maximum < 1:
            raise ValueError("claim limit must be positive")
        self._maximum = maximum
        self._claims: OrderedDict[int, str] = OrderedDict()

    def claim(self, event_id: int, task_id: str) -> str | None:
        existing = self._claims.get(event_id)
        if existing is not None:
            self._claims.move_to_end(event_id)
            return existing
        self._claims[event_id] = task_id
        if len(self._claims) > self._maximum:
            self._claims.popitem(last=False)
        return None

    def existing(self, event_id: int) -> str | None:
        return self._claims.get(event_id)

    def remember(self, event_id: int, task_id: str) -> None:
        self._claims[event_id] = task_id
        self._claims.move_to_end(event_id)
        if len(self._claims) > self._maximum:
            self._claims.popitem(last=False)


def new_record(
    request: DiscordTaskRequest,
    task_id: str,
    now: datetime,
    *,
    continued_from_task_id: str | None = None,
) -> DiscordTaskRecord:
    timestamp = as_timestamp(now)
    return DiscordTaskRecord(
        task_id=task_id,
        revision=0,
        owner_id=request.owner_id,
        guild_id=request.guild_id,
        origin_channel_id=request.origin_channel_id,
        execution_channel_id=request.execution_channel_id,
        trigger_event_id=request.trigger_event_id,
        source_message_id=request.source_message_id,
        card_message_id=None,
        result_message_id=None,
        source_kind=request.source_kind,
        source_label=request.source_label,
        created_at=timestamp,
        updated_at=timestamp,
        attempt=1,
        state=DiscordTaskState.STARTING,
        continued_from_task_id=continued_from_task_id,
    )


def persist_create(store: DiscordTaskStore, record: DiscordTaskRecord) -> None:
    try:
        store.create(record)
    except TaskStoreDurabilityError:
        if store.get(record.task_id) != record:
            raise


def persist_update(
    store: DiscordTaskStore,
    current: DiscordTaskRecord,
    update: Callable[[DiscordTaskRecord], DiscordTaskRecord],
) -> DiscordTaskRecord:
    candidate = update(current)
    try:
        return store.compare_and_set(
            current.task_id,
            current.revision,
            lambda _record: candidate,
        )
    except TaskStoreDurabilityError:
        canonical = store.get(current.task_id)
        expected = replace(candidate, revision=current.revision + 1)
        if canonical != expected:
            raise
        return canonical


def persist_link(
    store: DiscordTaskStore,
    parent: DiscordTaskRecord,
    child: DiscordTaskRecord,
) -> tuple[DiscordTaskRecord, DiscordTaskRecord]:
    try:
        return store.link_child(parent.task_id, parent.revision, child)
    except TaskStoreDurabilityError:
        canonical_parent = store.get(parent.task_id)
        canonical_child = store.get(child.task_id)
        if (
            canonical_parent.continued_to_task_id != child.task_id
            or canonical_child != child
        ):
            raise
        return canonical_parent, canonical_child


def as_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return value.astimezone(UTC).isoformat()
