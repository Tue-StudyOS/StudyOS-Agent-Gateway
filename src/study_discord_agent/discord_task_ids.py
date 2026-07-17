from collections.abc import Mapping
from uuid import UUID

from study_discord_agent.discord_task_model import DiscordTaskRecord


def lookup_task_key(tasks: Mapping[str, DiscordTaskRecord], task_id: str) -> str:
    if task_id in tasks:
        return task_id
    try:
        canonical = UUID(task_id).hex
    except (TypeError, ValueError) as error:
        raise KeyError(task_id) from error
    for key in tasks:
        if UUID(key).hex == canonical:
            return key
    raise KeyError(task_id)


def contains_task_id(tasks: Mapping[str, DiscordTaskRecord], task_id: str) -> bool:
    try:
        lookup_task_key(tasks, task_id)
    except KeyError:
        return False
    return True


def validate_unique_task_ids(tasks: Mapping[str, DiscordTaskRecord]) -> None:
    seen: set[str] = set()
    for key in tasks:
        canonical = UUID(key).hex
        if canonical in seen:
            raise ValueError("task identifiers must be unique UUIDs")
        seen.add(canonical)
