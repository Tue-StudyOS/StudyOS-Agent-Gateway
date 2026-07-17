from dataclasses import dataclass
from enum import StrEnum

from study_discord_agent.discord_task_model import DiscordTaskRecord


@dataclass(frozen=True)
class DiscordTaskAccess:
    actor_id: int
    guild_id: int
    visible_channel_ids: frozenset[int]
    manageable_channel_ids: frozenset[int]


class DiscordTaskAction(StrEnum):
    VIEW = "view"
    WHY_FAILED = "why_failed"
    STEER = "steer"
    STOP = "stop"
    RETRY = "retry"
    CONTINUE = "continue"
    FORGET = "forget"


class DiscordTaskAuthorizationError(PermissionError):
    pass


_OWNER_ACTIONS = frozenset(
    {
        DiscordTaskAction.WHY_FAILED,
        DiscordTaskAction.STEER,
        DiscordTaskAction.RETRY,
        DiscordTaskAction.CONTINUE,
        DiscordTaskAction.FORGET,
    }
)


def authorize(
    record: DiscordTaskRecord, action: DiscordTaskAction, access: DiscordTaskAccess
) -> None:
    if record.guild_id != access.guild_id:
        raise DiscordTaskAuthorizationError("task is not in this guild")
    if not _channels_are_visible(record, access):
        raise DiscordTaskAuthorizationError("task is no longer visible")
    if action is DiscordTaskAction.VIEW:
        return
    if action in _OWNER_ACTIONS and access.actor_id == record.owner_id:
        return
    if (
        action is DiscordTaskAction.STOP
        and (
            access.actor_id == record.owner_id
            or record.execution_channel_id in access.manageable_channel_ids
        )
    ):
        return
    raise DiscordTaskAuthorizationError("only the task owner may perform this action")


def _channels_are_visible(record: DiscordTaskRecord, access: DiscordTaskAccess) -> bool:
    return {
        record.origin_channel_id,
        record.execution_channel_id,
    }.issubset(access.visible_channel_ids)
