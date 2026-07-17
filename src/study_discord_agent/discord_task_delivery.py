import asyncio
import logging
from pathlib import Path
from typing import Protocol

from study_discord_agent.agent import AgentReply, ProgressSink
from study_discord_agent.discord_delivery_cache import DiscordDeliveryCache
from study_discord_agent.discord_delivery_resources import DiscordDeliveryLease
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_model import DiscordTaskRecord

logger = logging.getLogger(__name__)


class DiscordTaskDeliveryError(RuntimeError):
    def __init__(self, message: str, *, definitive_non_delivery: bool) -> None:
        super().__init__(message)
        self.definitive_non_delivery = definitive_non_delivery


class DiscordTaskPresentation(Protocol):
    async def create_card(self, record: DiscordTaskRecord) -> int | None: ...

    async def render_card(self, record: DiscordTaskRecord) -> None: ...

    async def prepare_reply(
        self, record: DiscordTaskRecord, reply: AgentReply
    ) -> PreparedDiscordReply: ...

    async def deliver_reply(
        self, record: DiscordTaskRecord, reply: PreparedDiscordReply
    ) -> int: ...

    def progress_sink(self, task_id: str) -> ProgressSink: ...

    async def close(self) -> None: ...


class DiscordTaskDelivery:
    """Own cache-to-send lease transfers for every Discord result attempt."""

    def __init__(
        self,
        cache: DiscordDeliveryCache,
        presentation: DiscordTaskPresentation,
        *,
        allowed_roots: tuple[Path, ...],
        max_bytes: int,
    ) -> None:
        self._cache = cache
        self._presentation = presentation
        self._allowed_roots = allowed_roots
        self._max_bytes = max_bytes
        self._active: dict[int, DiscordDeliveryLease] = {}

    def cache_and_consume(
        self, task_id: str, reply: PreparedDiscordReply
    ) -> PreparedDiscordReply | None:
        self.put(task_id, reply)
        return self.consume(task_id)

    def put(self, task_id: str, reply: PreparedDiscordReply) -> None:
        self._cache.put(task_id, reply)

    def consume(self, task_id: str) -> PreparedDiscordReply | None:
        return self._cache.consume(task_id, self._allowed_roots, self._max_bytes)

    async def send(self, record: DiscordTaskRecord, reply: PreparedDiscordReply) -> int:
        lease = reply.delivery_lease
        if lease is None:
            raise DiscordTaskDeliveryError(
                "Discord delivery requires a pinned lease",
                definitive_non_delivery=True,
            )
        lease_id = id(lease)
        self._active[lease_id] = lease
        ownership_resolved = False
        try:
            result_id = await self._presentation.deliver_reply(record, reply)
        except DiscordTaskDeliveryError as error:
            if error.definitive_non_delivery:
                try:
                    self._cache.restore(record.task_id, reply)
                except BaseException:
                    lease.close()
                    ownership_resolved = lease.closed
                    raise
                ownership_resolved = True
            else:
                lease.close()
                ownership_resolved = lease.closed
            raise
        except BaseException:
            lease.close()
            ownership_resolved = lease.closed
            raise
        else:
            try:
                lease.close()
            except BaseException:
                logger.warning(
                    "Discord result delivered but lease cleanup remains pending "
                    "task_id=%s",
                    record.task_id,
                )
            else:
                ownership_resolved = lease.closed
            return result_id
        finally:
            if ownership_resolved:
                self._active.pop(lease_id, None)

    def discard(self, task_id: str) -> None:
        self._cache.discard(task_id)

    def restore(self, task_id: str, reply: PreparedDiscordReply) -> None:
        self._cache.restore(task_id, reply)

    @staticmethod
    def close_reply(reply: PreparedDiscordReply) -> None:
        if reply.delivery_lease is not None:
            reply.delivery_lease.close()

    async def close(self) -> None:
        first_error: BaseException | None = None
        for lease_id, lease in tuple(self._active.items()):
            try:
                lease.close()
            except BaseException as error:
                first_error = first_error or error
            else:
                if lease.closed and self._active.get(lease_id) is lease:
                    self._active.pop(lease_id, None)
        try:
            await asyncio.to_thread(self._cache.close)
        except BaseException as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error
