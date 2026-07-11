import asyncio
import logging
import time
from dataclasses import dataclass

import discord

from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.discord_markdown import discord_safe_markdown

logger = logging.getLogger(__name__)


@dataclass
class _ProgressState:
    now: str = "Starting the task"
    completed: str | None = None
    next_step: str | None = None


class DiscordProgressMessage:
    def __init__(
        self,
        message: discord.Message,
        started_at: int,
        min_edit_interval_seconds: float = 5.0,
    ) -> None:
        self._message = message
        self._started_at = started_at
        self._min_edit_interval = min_edit_interval_seconds
        self._state = _ProgressState()
        self._last_edit_at = 0.0
        self._flush_task: asyncio.Task[None] | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        source: discord.Message,
        min_edit_interval_seconds: float = 5.0,
    ) -> "DiscordProgressMessage":
        started_at = int(time.time())
        content = _render(_ProgressState(), started_at)
        message = await source.reply(content)
        return cls(message, started_at, min_edit_interval_seconds)

    async def update(self, progress: AgentProgress) -> None:
        async with self._lock:
            if self._closed:
                return
            if progress.now:
                self._state.now = progress.now
            if progress.completed:
                self._state.completed = progress.completed
            if progress.next_step:
                self._state.next_step = progress.next_step
            self._schedule_flush_locked()

    async def note_steering(self) -> None:
        await self.update(AgentProgress(now="Applying the latest follow-up message"))

    async def update_for_queued_followup(self) -> None:
        await self.update(
            AgentProgress(now="Finishing the current turn", next_step="Apply queued follow-up")
        )

    async def fail(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._cancel_flush_locked()
            self._closed = True
            try:
                await self._message.edit(
                    content=(
                        "❌ **Agent failed**\nThe task could not be completed. Details were logged."
                    )
                )
            except discord.HTTPException as exc:
                logger.warning("failed to update Discord progress error state: %s", exc)

    async def delete(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._cancel_flush_locked()
            self._closed = True
            await self._message.delete()

    def _schedule_flush_locked(self) -> None:
        elapsed = time.monotonic() - self._last_edit_at
        if elapsed >= self._min_edit_interval:
            self._cancel_flush_locked()
            self._flush_task = asyncio.create_task(self._flush())
            return
        if self._flush_task is None or self._flush_task.done():
            delay = self._min_edit_interval - elapsed
            self._flush_task = asyncio.create_task(self._flush_after(delay))

    async def _flush_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            if self._closed:
                return
            try:
                await self._message.edit(content=_render(self._state, self._started_at))
                self._last_edit_at = time.monotonic()
            except discord.NotFound:
                self._closed = True
            except discord.HTTPException as exc:
                logger.warning("failed to edit Discord progress message: %s", exc)

    def _cancel_flush_locked(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None


def _render(state: _ProgressState, started_at: int) -> str:
    lines = [f"⏳ **Working** · started <t:{started_at}:R>", f"**Now:** {state.now}"]
    if state.completed:
        lines.append(f"**Completed:** {state.completed}")
    if state.next_step:
        lines.append(f"**Next:** {state.next_step}")
    return discord_safe_markdown("\n".join(lines))[:1900]
