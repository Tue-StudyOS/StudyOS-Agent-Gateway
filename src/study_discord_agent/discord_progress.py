import asyncio
import logging
import time
from dataclasses import dataclass

import discord

from study_discord_agent.agent_progress import AgentPlanStep, AgentProgress
from study_discord_agent.discord_markdown import discord_safe_markdown
from study_discord_agent.discord_progress_view import DiscordProgressView, StopHandler

logger = logging.getLogger(__name__)
SPINNER_FRAMES = ("-", "\\", "/", "|")


@dataclass
class _ProgressState:
    now: str = "Starting the task"
    completed: str | None = None
    next_step: str | None = None
    plan: tuple[AgentPlanStep, ...] | None = None
    spinner_frame: int = 0


class DiscordProgressMessage:
    def __init__(
        self,
        message: discord.Message,
        view: DiscordProgressView,
        started_at: int,
        min_edit_interval_seconds: float = 2.0,
        animation_interval_seconds: float = 2.0,
    ) -> None:
        self._message = message
        self._view = view
        self._started_at = started_at
        self._min_edit_interval = min_edit_interval_seconds
        self._state = _ProgressState()
        self._last_edit_at = 0.0
        self._flush_task: asyncio.Task[None] | None = None
        self._animation_interval = animation_interval_seconds
        self._animation_task: asyncio.Task[None] | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        source: discord.Message,
        on_stop: StopHandler,
        min_edit_interval_seconds: float = 2.0,
        animation_interval_seconds: float = 2.0,
    ) -> "DiscordProgressMessage":
        started_at = int(time.time())
        state = _ProgressState()
        view = DiscordProgressView(_render(state, started_at), source.author.id, on_stop)
        message = await source.reply(
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        progress = cls(
            message,
            view,
            started_at,
            min_edit_interval_seconds,
            animation_interval_seconds,
        )
        if animation_interval_seconds > 0:
            progress._animation_task = asyncio.create_task(progress._animate())
        return progress

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
            if progress.plan is not None:
                self._state.plan = progress.plan
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
            self._cancel_animation_locked()
            self._closed = True
            try:
                self._view.mark_failed(
                    "❌ **Agent failed**\nThe task couldn't be completed. Details were logged."
                )
                await self._message.edit(
                    view=self._view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._view.close()
            except discord.HTTPException as exc:
                logger.warning("failed to update Discord progress error state: %s", exc)

    async def delete(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._cancel_flush_locked()
            self._cancel_animation_locked()
            self._closed = True
            self._view.close()
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
                self._view.update_content(_render(self._state, self._started_at))
                await self._message.edit(
                    view=self._view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self._last_edit_at = time.monotonic()
            except discord.NotFound:
                self._closed = True
                self._cancel_animation_locked()
            except discord.HTTPException as exc:
                logger.warning("failed to edit Discord progress message: %s", exc)

    async def _animate(self) -> None:
        while True:
            await asyncio.sleep(self._animation_interval)
            async with self._lock:
                if self._closed:
                    return
                if not self._state.plan or not any(
                    item.status == "inProgress" for item in self._state.plan
                ):
                    continue
                self._state.spinner_frame = (
                    self._state.spinner_frame + 1
                ) % len(SPINNER_FRAMES)
                self._schedule_flush_locked()

    def _cancel_flush_locked(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    def _cancel_animation_locked(self) -> None:
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
        self._animation_task = None


def _render(state: _ProgressState, started_at: int) -> str:
    lines = [f"⏳ **Working** · started <t:{started_at}:R>"]
    if state.plan:
        lines.extend(("", "**Plan**", *_render_plan(state.plan, state.spinner_frame)))
    lines.extend(("", f"-# Now: {state.now}"))
    if state.plan:
        return discord_safe_markdown("\n".join(lines))[:3900]
    if state.completed:
        lines.append(f"**Completed:** {state.completed}")
    if state.next_step:
        lines.append(f"**Next:** {state.next_step}")
    return discord_safe_markdown("\n".join(lines))[:3900]


def _render_plan(plan: tuple[AgentPlanStep, ...], spinner_frame: int) -> list[str]:
    current = next(
        (index for index, item in enumerate(plan) if item.status == "inProgress"),
        len(plan) - 1,
    )
    start = max(0, min(current - 2, len(plan) - 6))
    visible = plan[start : start + 6]
    lines: list[str] = []
    if start:
        lines.append(f"-# … {start} earlier step{'s' if start != 1 else ''}")
    markers = {
        "completed": "`[x]`",
        "inProgress": f"`[{SPINNER_FRAMES[spinner_frame % len(SPINNER_FRAMES)]}]`",
        "pending": "`[ ]`",
    }
    for item in visible:
        lines.append(f"{markers.get(item.status, '`[ ]`')} {item.step}")
    remaining = len(plan) - start - len(visible)
    if remaining:
        lines.append(f"-# … {remaining} later step{'s' if remaining != 1 else ''}")
    return lines
