import discord

from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAuthorizationError,
)
from study_discord_agent.discord_task_model import DiscordTaskRecord
from study_discord_agent.discord_task_service_errors import DiscordTaskServiceClosed


def message_scope(message: discord.Message) -> tuple[int, int, int] | None:
    guild_id = getattr(message.guild, "id", None)
    channel_id = getattr(message.channel, "id", None)
    actor_id = getattr(message.author, "id", None)
    if type(guild_id) is not int or guild_id <= 0:
        return None
    if type(channel_id) is not int or channel_id <= 0:
        return None
    if type(actor_id) is not int or actor_id <= 0:
        return None
    return guild_id, channel_id, actor_id


def owner_access(
    record: DiscordTaskRecord,
    actor_id: int,
    guild_id: int,
    channel_id: int,
) -> DiscordTaskAccess:
    return DiscordTaskAccess(
        actor_id=actor_id,
        guild_id=guild_id,
        channel_id=channel_id,
        visible_channel_ids=frozenset({record.origin_channel_id, record.execution_channel_id}),
        manageable_channel_ids=frozenset(),
    )


def active_task_guidance(record: DiscordTaskRecord) -> str:
    next_step = " Start a new thread for a separate task."
    if record.card_message_id is None:
        return "A StudyOS task is already active in this channel." + next_step
    url = (
        f"https://discord.com/channels/{record.guild_id}/"
        f"{record.execution_channel_id}/{record.card_message_id}"
    )
    return f"A StudyOS task is already active: [open its task card]({url}).{next_step}"


def public_task_error(error: Exception) -> str:
    if isinstance(error, DiscordTaskAuthorizationError):
        return "Only the requester may control the active task."
    if isinstance(error, DiscordTaskServiceClosed):
        return "StudyOS task controls are shutting down. Try again later."
    return str(error)


async def reply_safely(message: discord.Message, content: str) -> None:
    await message.reply(
        content,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
