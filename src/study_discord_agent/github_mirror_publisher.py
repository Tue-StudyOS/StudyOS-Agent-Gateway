import logging
from collections.abc import Awaitable
from typing import Protocol, cast

import discord

from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError
from study_discord_agent.github_mirror_cards import github_mirror_view
from study_discord_agent.github_mirror_model import GitHubMirrorEvent, GitHubMirrorRecord
from study_discord_agent.github_mirror_store import GitHubMirrorStore

logger = logging.getLogger(__name__)
_RECONCILE_ATTEMPTS = 8


class GitHubMirrorConfigurationError(RuntimeError):
    pass


class GitHubMirrorChannelAccessError(RuntimeError):
    pass


class _ChannelClient(Protocol):
    def get_channel(self, channel_id: int, /) -> object | None: ...

    def fetch_channel(self, channel_id: int, /) -> Awaitable[object]: ...


class _MirrorMessage(Protocol):
    id: int

    def edit(self, **kwargs: object) -> Awaitable[object]: ...

    def delete(self) -> Awaitable[None]: ...


class _Guild(Protocol):
    id: int
    me: object | None


class _Permissions(Protocol):
    view_channel: bool
    send_messages: bool
    read_message_history: bool


class _MirrorChannel(Protocol):
    id: int
    type: discord.ChannelType
    guild: _Guild

    def permissions_for(self, member: object) -> _Permissions: ...

    def send(self, **kwargs: object) -> Awaitable[_MirrorMessage]: ...

    def fetch_message(self, message_id: int) -> Awaitable[_MirrorMessage]: ...


class GitHubMirrorPublisher:
    def __init__(
        self,
        client: _ChannelClient,
        store: GitHubMirrorStore,
        *,
        guild_id: int | None,
        channel_id: int | None,
    ) -> None:
        self._client = client
        self._store = store
        self._guild_id = guild_id
        self._channel_id = channel_id

    async def publish(self, event: GitHubMirrorEvent) -> GitHubMirrorRecord:
        channel = await self._resolve_channel()
        assert self._guild_id is not None
        assert self._channel_id is not None
        upsert = self._store.upsert_event(
            event,
            guild_id=self._guild_id,
            channel_id=self._channel_id,
        )
        record = upsert.record
        try:
            if record.card_message_id is None:
                return await self._create_card(channel, record)
            return await self._edit_card(channel, record)
        except Exception:
            logger.exception("GitHub mirror publication failed mirror_id=%s", record.mirror_id)
            raise

    async def _resolve_channel(self) -> _MirrorChannel:
        if self._guild_id is None or self._channel_id is None:
            raise GitHubMirrorConfigurationError(
                "DISCORD_GUILD_ID and DISCORD_PR_CHANNEL_ID are required for GitHub mirrors"
            )
        resolved = self._client.get_channel(self._channel_id)
        if resolved is None:
            try:
                resolved = await self._client.fetch_channel(self._channel_id)
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
        channel = cast(_MirrorChannel, resolved)
        if channel.type not in {discord.ChannelType.text, discord.ChannelType.news}:
            raise GitHubMirrorConfigurationError(
                "Configured Discord PR channel must be a guild text or announcement channel"
            )
        if channel.id != self._channel_id or channel.guild.id != self._guild_id:
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

    async def _create_card(
        self, channel: _MirrorChannel, record: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        try:
            message = await channel.send(
                content=None,
                view=github_mirror_view(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as error:
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
        if type(message.id) is not int or message.id <= 0:
            raise RuntimeError("Discord returned an invalid mirror card ID")
        try:
            retained, attached = self._store.attach_card_if_missing(record.mirror_id, message.id)
        except TaskStoreDurabilityError:
            await self._reconcile_card(message, record)
            raise
        except Exception:
            try:
                await message.delete()
            except Exception:
                logger.exception(
                    "failed to delete uncommitted GitHub mirror card mirror_id=%s",
                    record.mirror_id,
                )
            raise
        if not attached:
            await message.delete()
            logger.info("deleted raced GitHub mirror card mirror_id=%s", record.mirror_id)
            return retained
        retained = await self._reconcile_card(message, record)
        logger.info("published GitHub mirror card mirror_id=%s", record.mirror_id)
        return retained

    async def _edit_card(
        self, channel: _MirrorChannel, record: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        assert record.card_message_id is not None
        try:
            message = await channel.fetch_message(record.card_message_id)
        except discord.NotFound:
            cleared = self._store.clear_card_if_matches(record.mirror_id, record.card_message_id)
            if cleared.card_message_id is None:
                return await self._create_card(channel, cleared)
            return cleared
        except discord.Forbidden as error:
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
        await self._render_card(message, record)
        retained = await self._reconcile_card(message, record)
        logger.info("updated GitHub mirror card mirror_id=%s", record.mirror_id)
        return retained

    async def _reconcile_card(
        self, message: _MirrorMessage, rendered: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        rendered_revision = rendered.revision
        for _ in range(_RECONCILE_ATTEMPTS):
            canonical = self._store.get(rendered.mirror_id)
            if canonical.card_message_id != message.id:
                return canonical
            if canonical.revision != rendered_revision:
                await self._render_card(message, canonical)
                rendered_revision = canonical.revision
            latest = self._store.get(rendered.mirror_id)
            if latest.card_message_id != message.id:
                return latest
            if latest.revision == rendered_revision:
                return latest
        raise RuntimeError("GitHub mirror card did not reach a stable revision")

    async def _render_card(
        self, message: _MirrorMessage, record: GitHubMirrorRecord
    ) -> None:
        try:
            await message.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=github_mirror_view(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as error:
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
