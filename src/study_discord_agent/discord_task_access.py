from typing import Protocol, cast, runtime_checkable

import discord

from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAction,
    DiscordTaskAuthorizationError,
    authorize,
)
from study_discord_agent.discord_task_model import DiscordTaskRecord


class _Guild(Protocol):
    id: int

    def get_channel_or_thread(self, channel_id: int) -> object | None: ...

    async def fetch_channel(self, channel_id: int) -> object: ...


@runtime_checkable
class _Permissions(Protocol):
    view_channel: bool
    manage_messages: bool
    manage_threads: bool


@runtime_checkable
class _Channel(Protocol):
    id: int

    def permissions_for(self, member: object) -> object: ...


@runtime_checkable
class _PrivateThread(_Channel, Protocol):
    def is_private(self) -> bool: ...

    async def fetch_member(self, member_id: int) -> object: ...


async def resolve_task_access(
    interaction: discord.Interaction,
    record: DiscordTaskRecord,
) -> DiscordTaskAccess:
    guild = interaction.guild
    actor_id = getattr(interaction.user, "id", None)
    if (
        interaction.guild_id != record.guild_id
        or guild is None
        or getattr(guild, "id", None) != record.guild_id
        or not callable(getattr(guild, "get_channel_or_thread", None))
        or not callable(getattr(guild, "fetch_channel", None))
    ):
        raise DiscordTaskAuthorizationError("task is not in this guild")
    if type(actor_id) is not int or actor_id <= 0:
        raise DiscordTaskAuthorizationError("Discord member identity is unavailable")
    if interaction.channel_id not in {
        record.origin_channel_id,
        record.execution_channel_id,
    }:
        raise DiscordTaskAuthorizationError("task cannot be controlled from this channel")
    resolved_guild = cast(_Guild, guild)

    visible: set[int] = set()
    manageable: set[int] = set()
    for channel_id in {
        record.origin_channel_id,
        record.execution_channel_id,
    }:
        channel = await _resolve_channel(resolved_guild, channel_id)
        permissions = channel.permissions_for(interaction.user)
        if not isinstance(permissions, _Permissions) or not permissions.view_channel:
            continue
        if not await _has_private_thread_access(
            channel,
            actor_id,
            interaction.channel_id,
            permissions,
        ):
            continue
        visible.add(channel_id)
        if channel_id == record.execution_channel_id and permissions.manage_messages:
            manageable.add(channel_id)

    access = DiscordTaskAccess(
        actor_id=actor_id,
        guild_id=record.guild_id,
        channel_id=interaction.channel_id,
        visible_channel_ids=frozenset(visible),
        manageable_channel_ids=frozenset(manageable),
    )
    authorize(record, DiscordTaskAction.VIEW, access)
    return access


async def _resolve_channel(guild: _Guild, channel_id: int) -> _Channel:
    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.HTTPException, KeyError) as error:
            raise DiscordTaskAuthorizationError("task is no longer visible") from error
    if not isinstance(channel, _Channel) or channel.id != channel_id:
        raise DiscordTaskAuthorizationError("task is no longer visible")
    return channel


async def _has_private_thread_access(
    channel: _Channel,
    actor_id: int,
    current_channel_id: int | None,
    permissions: _Permissions,
) -> bool:
    if not isinstance(channel, _PrivateThread) or not channel.is_private():
        return True
    if channel.id == current_channel_id or permissions.manage_threads:
        return True
    try:
        member = await channel.fetch_member(actor_id)
    except discord.HTTPException:
        return False
    return getattr(member, "id", None) == actor_id
