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
    active = {task_id: task for task_id, task in tasks.items() if task.state in ACTIVE_STATES}
    inactive = [
        task
        for task in tasks.values()
        if task.state not in ACTIVE_STATES
        and _as_utc(datetime.fromisoformat(task.updated_at)) >= cutoff
    ]
    inactive.sort(
        key=lambda task: (_as_utc(datetime.fromisoformat(task.updated_at)), task.task_id),
        reverse=True,
    )
    retained = {task.task_id: task for task in inactive[:MAX_INACTIVE_TASKS]}
    retained.update(active)
    return retained


def same_task_scope(parent: DiscordTaskRecord, child: DiscordTaskRecord) -> bool:
    return (
        parent.owner_id == child.owner_id
        and parent.guild_id == child.guild_id
        and parent.origin_channel_id == child.origin_channel_id
        and parent.execution_channel_id == child.execution_channel_id
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return value.astimezone(UTC)
