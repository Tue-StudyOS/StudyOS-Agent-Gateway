from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskRecord,
    DiscordTaskState,
)

RETENTION_DAYS: Final = 30
MAX_INACTIVE_TASKS: Final = 500


def apply_retention(
    tasks: Mapping[str, DiscordTaskRecord], now: datetime
) -> dict[str, DiscordTaskRecord]:
    cutoff = _as_utc(now) - timedelta(days=RETENTION_DAYS)
    retained: dict[str, DiscordTaskRecord] = {}
    inactive: list[set[str]] = []
    for component in _components(tasks):
        records = [tasks[task_id] for task_id in component]
        if any(record.state in ACTIVE_STATES for record in records):
            retained.update({record.task_id: record for record in records})
        elif max(_updated_at(record) for record in records) >= cutoff:
            inactive.append(component)
    inactive.sort(key=lambda component: _component_key(component, tasks), reverse=True)
    inactive_count = 0
    for component in inactive:
        if inactive_count + len(component) > MAX_INACTIVE_TASKS:
            continue
        retained.update({task_id: tasks[task_id] for task_id in component})
        inactive_count += len(component)
    return retained


def same_task_scope(parent: DiscordTaskRecord, child: DiscordTaskRecord) -> bool:
    return (
        parent.owner_id == child.owner_id
        and parent.guild_id == child.guild_id
        and parent.origin_channel_id == child.origin_channel_id
        and parent.execution_channel_id == child.execution_channel_id
        and parent.intent is child.intent
        and parent.source_reference_id == child.source_reference_id
        and parent.repository_commit_sha == child.repository_commit_sha
    )


def validate_continuation_graph(tasks: Mapping[str, DiscordTaskRecord]) -> None:
    for record in tasks.values():
        if record.continued_to_task_id is not None:
            child = tasks.get(record.continued_to_task_id)
            if child is None or child.continued_from_task_id != record.task_id:
                raise ValueError("continuation child is missing or not reciprocal")
            if record.state is not DiscordTaskState.COMPLETED or not same_task_scope(record, child):
                raise ValueError("continuation link has an invalid parent or scope")
        if record.continued_from_task_id is not None:
            parent = tasks.get(record.continued_from_task_id)
            if parent is None or parent.continued_to_task_id != record.task_id:
                raise ValueError("continuation parent is missing or not reciprocal")
            if parent.state is not DiscordTaskState.COMPLETED or not same_task_scope(
                parent, record
            ):
                raise ValueError("continuation link has an invalid parent or scope")
    _assert_acyclic(tasks)


def _assert_acyclic(tasks: Mapping[str, DiscordTaskRecord]) -> None:
    visited: set[str] = set()
    for task_id in tasks:
        chain: set[str] = set()
        current = task_id
        while current is not None and current not in visited:
            if current in chain:
                raise ValueError("continuation links must be acyclic")
            chain.add(current)
            current = tasks[current].continued_to_task_id
        visited.update(chain)


def _components(tasks: Mapping[str, DiscordTaskRecord]) -> tuple[set[str], ...]:
    remaining = set(tasks)
    components: list[set[str]] = []
    while remaining:
        component = {remaining.pop()}
        pending = list(component)
        while pending:
            task_id = pending.pop()
            record = tasks[task_id]
            for linked in (record.continued_from_task_id, record.continued_to_task_id):
                if linked is not None and linked not in component:
                    component.add(linked)
                    remaining.discard(linked)
                    pending.append(linked)
        components.append(component)
    return tuple(components)


def _component_key(
    component: set[str], tasks: Mapping[str, DiscordTaskRecord]
) -> tuple[datetime, str]:
    newest = max((_updated_at(tasks[task_id]), task_id) for task_id in component)
    return newest


def _updated_at(record: DiscordTaskRecord) -> datetime:
    return _as_utc(datetime.fromisoformat(record.updated_at))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return value.astimezone(UTC)
