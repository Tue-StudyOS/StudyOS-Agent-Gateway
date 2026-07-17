import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from study_discord_agent.agent_progress import AgentProgress

logger = logging.getLogger(__name__)
ProgressRenderer = Callable[[str, AgentProgress], Awaitable[None]]


@dataclass
class _ProgressEntry:
    progress: AgentProgress
    version: int = 1
    last_render_at: float = 0.0
    flush_task: asyncio.Task[None] | None = None


class DiscordTaskProgressCoordinator:
    """Merge and throttle progress while retaining the newest complete snapshot."""

    def __init__(
        self,
        render: ProgressRenderer,
        *,
        min_edit_interval_seconds: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_edit_interval_seconds < 0:
            raise ValueError("min_edit_interval_seconds must be non-negative")
        self._render = render
        self._min_interval = min_edit_interval_seconds
        self._monotonic = monotonic
        self._entries: dict[str, _ProgressEntry] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def update(self, task_id: str, update: AgentProgress) -> None:
        async with self._lock:
            if self._closed:
                return
            entry = self._entries.get(task_id)
            if entry is None:
                entry = _ProgressEntry(progress=update)
                self._entries[task_id] = entry
            else:
                entry.progress = _merge(entry.progress, update)
                entry.version += 1
            if entry.flush_task is None or entry.flush_task.done():
                delay = max(
                    0.0,
                    self._min_interval - (self._monotonic() - entry.last_render_at),
                )
                entry.flush_task = asyncio.create_task(self._flush_after(task_id, delay))

    async def snapshot(self, task_id: str) -> AgentProgress | None:
        async with self._lock:
            entry = self._entries.get(task_id)
            return entry.progress if entry is not None else None

    async def finish(self, task_id: str) -> None:
        async with self._lock:
            entry = self._entries.pop(task_id, None)
            if entry is not None:
                _cancel_unless_current(entry.flush_task)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = [
                entry.flush_task
                for entry in self._entries.values()
                if entry.flush_task is not None
            ]
            self._entries.clear()
            for task in tasks:
                _cancel_unless_current(task)
        current = asyncio.current_task()
        await asyncio.gather(
            *(task for task in tasks if task is not current),
            return_exceptions=True,
        )

    async def _flush_after(self, task_id: str, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        async with self._lock:
            entry = self._entries.get(task_id)
            if self._closed or entry is None:
                return
            progress = entry.progress
            rendered_version = entry.version
        try:
            await self._render(task_id, progress)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Discord task progress render failed task_id=%s", task_id)
        async with self._lock:
            current = self._entries.get(task_id)
            if current is None or current is not entry:
                return
            current.last_render_at = self._monotonic()
            current.flush_task = None
            if current.version != rendered_version and not self._closed:
                current.flush_task = asyncio.create_task(
                    self._flush_after(task_id, self._min_interval)
                )


def _merge(current: AgentProgress, update: AgentProgress) -> AgentProgress:
    return AgentProgress(
        now=update.now if update.now is not None else current.now,
        completed=(
            update.completed if update.completed is not None else current.completed
        ),
        next_step=(
            update.next_step if update.next_step is not None else current.next_step
        ),
        plan=update.plan if update.plan is not None else current.plan,
    )


def _cancel_unless_current(task: asyncio.Task[None] | None) -> None:
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
