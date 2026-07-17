from collections.abc import AsyncIterator, Awaitable
from typing import Protocol, cast

import discord

CREATE_SEARCH_LIMIT = 100


class GitHubMirrorConfigurationError(RuntimeError):
    pass


class GitHubMirrorChannelAccessError(RuntimeError):
    pass


class MirrorChannelClient(Protocol):
    def get_channel(self, channel_id: int, /) -> object | None: ...

    def fetch_channel(self, channel_id: int, /) -> Awaitable[object]: ...


class MirrorMessage(Protocol):
    id: int
    nonce: str | int | None
    author: object

    def edit(self, **kwargs: object) -> Awaitable[object]: ...

    def delete(self) -> Awaitable[None]: ...


class _Guild(Protocol):
    id: int
    me: object | None


class _Permissions(Protocol):
    view_channel: bool
    send_messages: bool
    read_message_history: bool


class MirrorChannel(Protocol):
    id: int
    type: discord.ChannelType
    guild: _Guild

    def permissions_for(self, member: object) -> _Permissions: ...

    def send(self, **kwargs: object) -> Awaitable[MirrorMessage]: ...

    def fetch_message(self, message_id: int) -> Awaitable[MirrorMessage]: ...

    def history(self, *, limit: int) -> AsyncIterator[MirrorMessage]: ...


async def resolve_mirror_channel(
    client: MirrorChannelClient,
    *,
    guild_id: int | None,
    channel_id: int | None,
) -> MirrorChannel:
    if guild_id is None or channel_id is None:
        raise GitHubMirrorConfigurationError(
            "DISCORD_GUILD_ID and DISCORD_PR_CHANNEL_ID are required for GitHub mirrors"
        )
    resolved = client.get_channel(channel_id)
    if resolved is None:
        try:
            resolved = await client.fetch_channel(channel_id)
        except discord.NotFound as error:
            raise GitHubMirrorConfigurationError(
                "Configured Discord PR channel does not exist"
            ) from error
        except discord.Forbidden as error:
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
    if not isinstance(resolved, discord.abc.Messageable):
        raise GitHubMirrorConfigurationError("Configured Discord PR channel is not messageable")
    channel = cast(MirrorChannel, resolved)
    if channel.type not in {discord.ChannelType.text, discord.ChannelType.news}:
        raise GitHubMirrorConfigurationError(
            "Configured Discord PR channel must be a guild text or announcement channel"
        )
    if channel.id != channel_id or channel.guild.id != guild_id:
        raise GitHubMirrorConfigurationError(
            "Configured Discord PR channel is outside the configured guild"
        )
    member = channel.guild.me
    if member is None:
        raise GitHubMirrorChannelAccessError("Discord bot guild membership is unavailable")
    permissions = channel.permissions_for(member)
    if not all(
        (
            permissions.view_channel,
            permissions.send_messages,
            permissions.read_message_history,
        )
    ):
        raise GitHubMirrorChannelAccessError(
            "Discord bot needs view, send, and message-history permissions"
        )
    return channel


async def find_bot_nonce_messages(channel: MirrorChannel, nonce: str) -> tuple[MirrorMessage, ...]:
    member = channel.guild.me
    member_id = getattr(member, "id", None)
    if type(member_id) is not int or member_id <= 0:
        raise GitHubMirrorChannelAccessError("Discord bot guild membership is unavailable")
    matches: list[MirrorMessage] = []
    try:
        async for message in channel.history(limit=CREATE_SEARCH_LIMIT):
            author_id = getattr(message.author, "id", None)
            if author_id == member_id and message.nonce == nonce:
                matches.append(message)
    except discord.Forbidden as error:
        raise GitHubMirrorChannelAccessError(
            "Configured Discord PR channel is inaccessible"
        ) from error
    return tuple(matches)
