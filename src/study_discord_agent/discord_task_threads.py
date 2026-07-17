from dataclasses import dataclass, field
from typing import Protocol, cast

import discord

from study_discord_agent.discord_origin import DiscordOriginContext


class DiscordTaskThreadError(RuntimeError):
    pass


class _ThreadParent(Protocol):
    id: int
    name: str
    type: discord.ChannelType

    def permissions_for(self, member: object) -> object: ...

    async def create_thread(self, **kwargs: object) -> object: ...


class _CreatedThread(Protocol):
    id: int
    name: str
    parent_id: int | None
    category_id: int | None

    async def delete(self, *, reason: str | None = None) -> object: ...


@dataclass(frozen=True)
class DedicatedTaskThread:
    id: int
    name: str
    category_id: int | None
    channel: _CreatedThread = field(repr=False, compare=False)


async def create_dedicated_thread(
    interaction: discord.Interaction,
) -> DedicatedTaskThread:
    channel_obj = interaction.channel
    guild = interaction.guild
    bot_member = getattr(guild, "me", None)
    if (
        channel_obj is None
        or getattr(channel_obj, "type", None) is not discord.ChannelType.text
        or not callable(getattr(channel_obj, "permissions_for", None))
        or not callable(getattr(channel_obj, "create_thread", None))
        or bot_member is None
    ):
        raise DiscordTaskThreadError(
            "A dedicated task thread can only be created from a guild text channel."
        )
    channel = cast(_ThreadParent, channel_obj)
    bot_permissions = channel.permissions_for(bot_member)
    actor_permissions = channel.permissions_for(interaction.user)
    if (
        getattr(actor_permissions, "create_public_threads", False) is not True
        or getattr(actor_permissions, "send_messages_in_threads", False) is not True
    ):
        raise DiscordTaskThreadError(
            "You cannot create and use a public task thread in this channel."
        )
    if (
        getattr(bot_permissions, "create_public_threads", False) is not True
        or getattr(bot_permissions, "send_messages_in_threads", False) is not True
    ):
        raise DiscordTaskThreadError(
            "StudyOS cannot create and use a public task thread in this channel."
        )
    try:
        thread = await channel.create_thread(
            name="studyos-task",
            type=discord.ChannelType.public_thread,
            auto_archive_duration=1440,
            reason="StudyOS dedicated task",
        )
    except discord.HTTPException as error:
        raise DiscordTaskThreadError(
            "StudyOS could not create the requested task thread."
        ) from error
    thread_id = getattr(thread, "id", None)
    thread_name = getattr(thread, "name", None)
    if (
        type(thread_id) is not int
        or thread_id <= 0
        or not isinstance(thread_name, str)
        or getattr(thread, "parent_id", None) != channel.id
        or not callable(getattr(thread, "delete", None))
    ):
        raise DiscordTaskThreadError(
            "Discord did not return the requested dedicated task thread."
        )
    category_id = getattr(thread, "category_id", None)
    return DedicatedTaskThread(
        id=thread_id,
        name=thread_name,
        category_id=category_id if isinstance(category_id, int) else None,
        channel=cast(_CreatedThread, thread),
    )


async def delete_dedicated_thread(thread: DedicatedTaskThread) -> None:
    try:
        await thread.channel.delete(reason="StudyOS task startup failed")
    except discord.HTTPException as error:
        raise DiscordTaskThreadError(
            "StudyOS could not remove the unused task thread."
        ) from error


def interaction_scope(interaction: discord.Interaction) -> tuple[int, int, int]:
    owner_id = getattr(interaction.user, "id", None)
    if (
        interaction.guild_id is None
        or interaction.guild is None
        or interaction.channel_id is None
        or interaction.channel is None
        or type(owner_id) is not int
        or owner_id <= 0
    ):
        raise DiscordTaskThreadError(
            "StudyOS tasks are available only in server channels."
        )
    return interaction.guild_id, interaction.channel_id, owner_id


def channel_context(channel: object | None) -> DiscordOriginContext | None:
    channel_id = getattr(channel, "id", None)
    if type(channel_id) is not int:
        return None
    return DiscordOriginContext(
        channel_id=channel_id,
        channel_name=getattr(channel, "name", None),
        channel_type=type(channel).__name__,
        thread_id=channel_id if isinstance(channel, discord.Thread) else None,
        parent_channel_id=getattr(channel, "parent_id", None),
        category_id=getattr(channel, "category_id", None),
    )
