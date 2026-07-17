import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import discord

from study_discord_agent.agent import AgentGateway, AgentReply
from study_discord_agent.codex_app_server_runtime import AgentTurnInterrupted, SteerResult
from study_discord_agent.config import Settings
from study_discord_agent.discord_files import (
    DISCORD_MESSAGE_LIMIT,
    save_message_attachments,
    validate_artifact_files,
)
from study_discord_agent.discord_markdown import discord_safe_markdown
from study_discord_agent.discord_message_context import is_cancel_prompt
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_progress import DiscordProgressMessage
from study_discord_agent.discord_reply_content import prepare_discord_reply

logger = logging.getLogger(__name__)
MAX_SEEN_MESSAGE_IDS = 2048


@dataclass
class _ActiveMention:
    task: asyncio.Task[None]
    owner_id: int
    progress: DiscordProgressMessage | None = None


class DiscordMentionCoordinator:
    def __init__(self, settings: Settings, agent: AgentGateway) -> None:
        self._settings = settings
        self._agent = agent
        self._active: dict[int, _ActiveMention] = {}
        self._lock = asyncio.Lock()
        self._seen_ids: set[int] = set()
        self._seen_order: deque[int] = deque()

    async def dispatch(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
        *,
        start_if_idle: bool = True,
    ) -> bool:
        channel_id = message.channel.id
        async with self._lock:
            if message.id in self._seen_ids:
                logger.info("duplicate discord mention ignored message_id=%s", message.id)
                return False
            self._remember(message.id)
            active = self._active.get(channel_id)
            if active and active.task.done():
                self._active.pop(channel_id, None)
                active = None

        if not start_if_idle and (
            active is None or active.owner_id != message.author.id
        ):
            return False
        expected_followup_task = active.task if not start_if_idle and active else None

        if is_cancel_prompt(prompt):
            interrupted = await self.stop(channel_id)
            response = (
                "Stopped the active task in this channel."
                if interrupted
                else "No active task is running in this channel."
            )
            await message.reply(response)
            return True

        while True:
            active = await self._current_active(channel_id)
            if not start_if_idle and (
                active is None or active.task is not expected_followup_task
            ):
                return False
            if active:
                if await self._steer(active, message, prompt, origin_context):
                    return True
                await active.task
                continue
            if not start_if_idle:
                return False
            if await self._start(message, prompt, origin_context):
                return True

    async def stop(
        self,
        channel_id: int,
        *,
        expected_task: asyncio.Task[None] | None = None,
    ) -> bool:
        active = await self._current_active(channel_id)
        if active is None or (expected_task is not None and active.task is not expected_task):
            return False
        interrupted = await self._agent.interrupt(channel_id)
        if not interrupted and not active.task.done():
            current = await self._current_active(channel_id)
            if current is None or current.task is not active.task:
                return False
            active.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active.task
            interrupted = True
        return interrupted

    async def _start(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> bool:
        channel_id = message.channel.id
        async with self._lock:
            existing = self._active.get(channel_id)
            if existing and not existing.task.done():
                return False
            task = asyncio.create_task(self._run(message, prompt, origin_context))
            active = _ActiveMention(task=task, owner_id=message.author.id)
            self._active[channel_id] = active
            task.add_done_callback(lambda done: asyncio.create_task(self._forget(channel_id, done)))
            return True

    async def _current_active(self, channel_id: int) -> _ActiveMention | None:
        async with self._lock:
            active = self._active.get(channel_id)
            if active and active.task.done():
                self._active.pop(channel_id, None)
                return None
            return active

    async def _steer(
        self,
        active: _ActiveMention,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> bool:
        attachments = await save_message_attachments(
            message,
            Path(self._settings.discord_attachment_dir),
        )
        result = await self._agent.steer(
            prompt=prompt,
            user=str(message.author),
            channel_id=message.channel.id,
            source_message_id=message.id,
            attachment_paths=attachments,
            origin_context=origin_context,
        )
        if result is not SteerResult.STEERED:
            if active.progress:
                await active.progress.update_for_queued_followup()
            return False
        if active.progress:
            await active.progress.note_steering()
        logger.info(
            "discord follow-up steered channel_id=%s message_id=%s",
            message.channel.id,
            message.id,
        )
        return True

    async def _run(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> None:
        progress: DiscordProgressMessage | None = None
        try:
            current_task = asyncio.current_task()
            if current_task is None:
                raise RuntimeError("Discord task has no active asyncio task")
            progress = await DiscordProgressMessage.create(
                message,
                on_stop=lambda: self.stop(
                    message.channel.id,
                    expected_task=current_task,
                ),
            )
            await self._set_progress(message.channel.id, progress)
            attachments = await save_message_attachments(
                message,
                Path(self._settings.discord_attachment_dir),
            )
            reply = await self._agent.ask(
                prompt=prompt,
                user=str(message.author),
                channel_id=message.channel.id,
                source_message_id=message.id,
                attachment_paths=attachments,
                origin_context=origin_context,
                on_progress=progress.update,
            )
            await _deliver_reply(message, reply, self._settings)
            await _delete_progress(progress)
            logger.info("discord mention replied message_id=%s", message.id)
        except AgentTurnInterrupted:
            if progress:
                await _delete_progress(progress)
            logger.info("discord mention interrupted message_id=%s", message.id)
        except asyncio.CancelledError:
            if progress:
                await _delete_progress(progress)
            logger.info("discord mention cancelled message_id=%s", message.id)
            raise
        except Exception as exc:
            if progress:
                await progress.fail()
            else:
                await message.reply(f"Agent failed: {exc}")
            logger.warning("discord mention failed message_id=%s error=%s", message.id, exc)

    async def _set_progress(
        self,
        channel_id: int,
        progress: DiscordProgressMessage,
    ) -> None:
        task = asyncio.current_task()
        async with self._lock:
            active = self._active.get(channel_id)
            if active and active.task is task:
                active.progress = progress

    async def _forget(self, channel_id: int, task: asyncio.Task[None]) -> None:
        async with self._lock:
            if (active := self._active.get(channel_id)) and active.task is task:
                self._active.pop(channel_id, None)

    def _remember(self, message_id: int) -> None:
        self._seen_ids.add(message_id)
        self._seen_order.append(message_id)
        while len(self._seen_order) > MAX_SEEN_MESSAGE_IDS:
            self._seen_ids.discard(self._seen_order.popleft())


async def _deliver_reply(
    message: discord.Message,
    reply: AgentReply,
    settings: Settings,
) -> None:
    roots = tuple(Path(root) for root in settings.discord_artifact_allowed_root_list)
    if not roots:
        raise RuntimeError("DISCORD_ARTIFACT_ALLOWED_ROOTS must contain at least one path")
    prepared = prepare_discord_reply(reply.message, reply.files, roots[0], str(message.id))
    if not prepared.files:
        await message.reply(_discord_text(prepared.message))
        return
    files: list[discord.File] = []
    try:
        paths = validate_artifact_files(
            prepared.files,
            roots,
            settings.discord_artifact_max_bytes,
        )
        files = [discord.File(path) for path in paths]
        await message.reply(content=_discord_text(prepared.message) or None, files=files)
    finally:
        for file in files:
            file.close()
        if prepared.generated_file:
            try:
                prepared.generated_file.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("failed to clean generated Discord reply attachment: %s", exc)


async def _delete_progress(progress: DiscordProgressMessage) -> None:
    try:
        await progress.delete()
    except discord.HTTPException as exc:
        logger.warning("failed to delete Discord progress message: %s", exc)


def _discord_text(message: str) -> str:
    return discord_safe_markdown(message)[:DISCORD_MESSAGE_LIMIT]
