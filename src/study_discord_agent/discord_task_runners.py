import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from study_discord_agent.discord_task_service_errors import DiscordTaskServiceClosed

logger = logging.getLogger(__name__)


class DiscordTaskRunners:
    def __init__(self) -> None:
        self._current: dict[str, asyncio.Task[None]] = {}
        self._all: set[asyncio.Task[None]] = set()
        self._closed = False

    def spawn(self, task_id: str, coroutine: Coroutine[Any, Any, None]) -> None:
        try:
            self.ensure_open()
        except DiscordTaskServiceClosed:
            coroutine.close()
            raise
        existing = self._current.get(task_id)
        if existing is not None and not existing.done():
            coroutine.close()
            raise RuntimeError("Discord task already has a runner")
        runner = asyncio.create_task(coroutine, name=f"discord-task:{task_id}")
        self._current[task_id] = runner
        self._all.add(runner)
        runner.add_done_callback(lambda done: self._done(task_id, done))

    async def wait_idle(self, task_id: str) -> None:
        runner = self._current.get(task_id)
        if runner is not None:
            await asyncio.gather(runner, return_exceptions=True)
        self.ensure_open()

    def ensure_open(self) -> None:
        if self._closed:
            raise DiscordTaskServiceClosed("Discord task service is closed")

    async def close(self) -> None:
        self._closed = True
        runners = tuple(self._all)
        for runner in runners:
            runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    def _done(self, task_id: str, runner: asyncio.Task[None]) -> None:
        if self._current.get(task_id) is runner:
            self._current.pop(task_id, None)
        self._all.discard(runner)
        if not runner.cancelled() and runner.exception() is not None:
            logger.error("Discord task runner failed task_id=%s", task_id)
