from collections.abc import Mapping
from dataclasses import replace

from study_discord_agent.discord_task_model import ACTIVE_STATES, DiscordTaskRecord
from study_discord_agent.discord_task_store_policy import validate_continuation_graph


def forget_inactive_task(
    tasks: Mapping[str, DiscordTaskRecord],
    task_id: str,
    *,
    updated_at: str,
) -> tuple[dict[str, DiscordTaskRecord], tuple[DiscordTaskRecord, ...]]:
    target = tasks[task_id]
    if target.state in ACTIVE_STATES:
        raise ValueError("active tasks cannot be forgotten")
    updated = dict(tasks)
    del updated[task_id]
    neighbors: list[DiscordTaskRecord] = []
    if target.continued_from_task_id is not None:
        parent = updated[target.continued_from_task_id]
        parent = replace(
            parent,
            continued_to_task_id=None,
            revision=parent.revision + 1,
            updated_at=updated_at,
        )
        updated[parent.task_id] = parent
        neighbors.append(parent)
    if target.continued_to_task_id is not None:
        child = updated[target.continued_to_task_id]
        child = replace(
            child,
            continued_from_task_id=None,
            revision=child.revision + 1,
            updated_at=updated_at,
        )
        updated[child.task_id] = child
        neighbors.append(child)
    validate_continuation_graph(updated)
    return updated, tuple(neighbors)
