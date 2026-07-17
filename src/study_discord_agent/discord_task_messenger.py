import asyncio
from collections.abc import Awaitable, Callable
from io import BufferedIOBase
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from weakref import WeakValueDictionary

import discord

from study_discord_agent.agent import AgentReply, ProgressSink
from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.discord_delivery_resources import PinnedDiscordFile
from study_discord_agent.discord_markdown import discord_safe_markdown
from study_discord_agent.discord_reply_content import (
    PreparedDiscordReply,
    prepare_discord_reply,
)
from study_discord_agent.discord_task_cards import build_task_card
from study_discord_agent.discord_task_delivery import DiscordTaskDeliveryError
from study_discord_agent.discord_task_model import ACTIVE_STATES, DiscordTaskRecord
from study_discord_agent.discord_task_progress import DiscordTaskProgressCoordinator
from study_discord_agent.discord_task_service_errors import DiscordTaskControlState

DISCORD_MESSAGE_LIMIT = 2_000
ControlResolver = Callable[
    [DiscordTaskRecord], Awaitable[DiscordTaskControlState]
]


class _TaskStore(Protocol):
    def get(self, task_id: str) -> DiscordTaskRecord: ...


class _DiscordClient(Protocol):
    def get_channel(self, channel_id: int) -> object | None: ...

    async def fetch_channel(self, channel_id: int) -> object: ...


@runtime_checkable
class _MessageChannel(Protocol):
    async def send(self, **kwargs: object) -> object: ...

    async def fetch_message(self, message_id: int) -> object: ...


@runtime_checkable
class _DiscordMessage(Protocol):
    id: int

    async def edit(self, **kwargs: object) -> object: ...


class DiscordTaskCardMessenger:
    """Discord I/O for persistent task cards and pinned result delivery."""

    def __init__(
        self,
        *,
        client: _DiscordClient,
        store: _TaskStore,
        resolve_controls: ControlResolver,
        artifact_root: Path,
        min_edit_interval_seconds: float = 2.0,
    ) -> None:
        self._client = client
        self._store = store
        self._resolve_controls = resolve_controls
        self._artifact_root = artifact_root
        self._render_locks = WeakValueDictionary[str, asyncio.Lock]()
        self._progress = DiscordTaskProgressCoordinator(
            self._render_progress,
            min_edit_interval_seconds=min_edit_interval_seconds,
        )

    async def create_card(self, record: DiscordTaskRecord) -> int:
        channel = await self._channel(record.execution_channel_id)
        controls = await self._resolve_controls(record)
        message = await channel.send(
            view=build_task_card(record, None, controls),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return _message_id(message)

    async def render_card(self, record: DiscordTaskRecord) -> None:
        await self._render_current(record.task_id)

    async def prepare_reply(
        self,
        record: DiscordTaskRecord,
        reply: AgentReply,
    ) -> PreparedDiscordReply:
        return prepare_discord_reply(
            reply.message,
            reply.files,
            self._artifact_root,
            record.task_id,
        )

    async def deliver_reply(
        self,
        record: DiscordTaskRecord,
        reply: PreparedDiscordReply,
    ) -> int:
        lease = reply.delivery_lease
        if lease is None:
            raise DiscordTaskDeliveryError(
                "Discord result has no pinned delivery lease",
                definitive_non_delivery=True,
            )
        files: list[discord.File] = []
        try:
            channel = await self._channel(record.execution_channel_id)
            for resource in lease.files:
                files.append(_delivery_file(resource))
        except Exception as error:
            for file in files:
                file.close()
            raise DiscordTaskDeliveryError(
                "Discord result could not be prepared for sending",
                definitive_non_delivery=True,
            ) from error
        try:
            message = await channel.send(
                content=_reply_text(reply.message),
                files=files,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            raise DiscordTaskDeliveryError(
                "Discord result delivery failed",
                definitive_non_delivery=_definitive_send_failure(error),
            ) from error
        finally:
            for file in files:
                file.close()
        return _message_id(message)

    def progress_sink(self, task_id: str) -> ProgressSink:
        async def sink(progress: AgentProgress) -> None:
            try:
                record = self._store.get(task_id)
            except KeyError:
                return
            if record.state in ACTIVE_STATES and record.card_message_id is not None:
                await self._progress.update(task_id, progress)

        return cast(ProgressSink, sink)

    async def close(self) -> None:
        await self._progress.close()

    async def _render_progress(
        self,
        task_id: str,
        progress: AgentProgress,
    ) -> None:
        await self._render_current(task_id, progress)

    async def _render_current(
        self,
        task_id: str,
        progress: AgentProgress | None = None,
    ) -> None:
        lock = self._render_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            current = self._store.get(task_id)
            if current.card_message_id is None:
                return
            if current.state not in ACTIVE_STATES:
                await self._progress.finish(task_id)
                progress = None
            elif progress is None:
                progress = await self._progress.snapshot(task_id)
            controls = await self._resolve_controls(current)
            channel = await self._channel(current.execution_channel_id)
            message = await channel.fetch_message(current.card_message_id)
            if not isinstance(message, _DiscordMessage):
                raise RuntimeError("Discord task card message is not editable")
            await message.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=build_task_card(current, progress, controls),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _channel(self, channel_id: int) -> _MessageChannel:
        channel = self._client.get_channel(channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(channel_id)
        if not isinstance(channel, _MessageChannel):
            raise RuntimeError("Discord task channel is not messageable")
        return channel


def _message_id(message: object) -> int:
    message_id = getattr(message, "id", None)
    if type(message_id) is not int or message_id <= 0:
        raise RuntimeError("Discord returned an invalid message identifier")
    return message_id


def _reply_text(message: str) -> str | None:
    rendered = discord_safe_markdown(message)[:DISCORD_MESSAGE_LIMIT]
    return rendered or None


def _definitive_send_failure(error: BaseException) -> bool:
    if isinstance(error, (discord.Forbidden, discord.NotFound, discord.RateLimited)):
        return True
    if isinstance(error, discord.HTTPException):
        return error.status < 500
    return False


def _delivery_file(resource: PinnedDiscordFile) -> discord.File:
    if not isinstance(resource.stream, BufferedIOBase):
        raise TypeError("Discord pinned resource is not a buffered stream")
    return discord.File(resource.stream, filename=resource.filename)
