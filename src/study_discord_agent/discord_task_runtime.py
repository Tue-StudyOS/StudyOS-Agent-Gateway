import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from weakref import WeakValueDictionary

from study_discord_agent.agent import AgentExecutionContext, AgentGateway, AgentReply
from study_discord_agent.agent_errors import (
    AgentWorkspaceOrAttachmentError,
)
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_delivery import (
    DiscordTaskDelivery,
    DiscordTaskDeliveryError,
    DiscordTaskPresentation,
)
from study_discord_agent.discord_task_failures import classify_delivery_failure
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
    DiscordTaskState,
    transition,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_runners import DiscordTaskRunners
from study_discord_agent.discord_task_runtime_failures import record_agent_failure
from study_discord_agent.discord_task_service_state import as_timestamp, persist_update
from study_discord_agent.discord_task_store import DiscordTaskStore, TaskRevisionConflict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunSpec:
    prompt: str
    source_message_id: int | None
    attachments: StagedDiscordAttachments
    origin_context: DiscordOriginContext | None
    recovering: bool = False
    create_card: bool = False
    require_existing_session: bool = False

    @classmethod
    def from_request(cls, request: DiscordTaskRequest) -> "AgentRunSpec":
        return cls(
            prompt=request.prompt,
            source_message_id=request.source_message_id,
            attachments=request.attachments,
            origin_context=request.origin_context,
            create_card=True,
            require_existing_session=(
                request.source_kind is DiscordTaskSourceKind.CONTINUATION
            ),
        )

    @classmethod
    def for_recovery(cls, prompt: str) -> "AgentRunSpec":
        return cls(
            prompt=prompt,
            source_message_id=None,
            attachments=StagedDiscordAttachments(paths=(), directory=None),
            origin_context=None,
            recovering=True,
            require_existing_session=True,
        )


class DiscordTaskRuntime:
    def __init__(
        self,
        *,
        agent: AgentGateway,
        store: DiscordTaskStore,
        presentation: DiscordTaskPresentation,
        delivery: DiscordTaskDelivery,
        timestamp: Callable[[], datetime],
    ) -> None:
        self._agent = agent
        self._store = store
        self._presentation = presentation
        self._delivery = delivery
        self._timestamp = timestamp
        self._runners = DiscordTaskRunners()
        self._render_locks = WeakValueDictionary[str, asyncio.Lock]()

    def spawn_agent(self, task_id: str, spec: AgentRunSpec) -> None:
        self._runners.spawn(task_id, self._agent_runner(task_id, spec))

    async def wait_idle(self, task_id: str) -> None:
        await self._runners.wait_idle(task_id)

    def ensure_open(self) -> None:
        self._runners.ensure_open()

    def consume_delivery(self, task_id: str) -> PreparedDiscordReply | None:
        return self._delivery.consume(task_id)

    def restore_delivery(self, task_id: str, reply: PreparedDiscordReply) -> None:
        self._delivery.restore(task_id, reply)

    def spawn_delivery(self, task_id: str, reply: PreparedDiscordReply) -> None:
        self._runners.spawn(task_id, self._send_delivery(task_id, reply))

    def discard(self, task_id: str) -> None:
        self._delivery.discard(task_id)

    async def render(self, record: DiscordTaskRecord) -> None:
        lock = self._render_locks.setdefault(record.task_id, asyncio.Lock())
        async with lock:
            try:
                current = self._store.get(record.task_id)
                await self._presentation.render_card(current)
            except Exception:
                logger.warning("Discord task card render failed task_id=%s", record.task_id)

    async def close(self) -> None:
        await self._runners.close()
        await self._delivery.close()

    async def _agent_runner(self, task_id: str, spec: AgentRunSpec) -> None:
        try:
            if spec.create_card:
                await self._create_card(task_id)
            if spec.recovering:
                await self._agent.start()
                current = self._store.get(task_id)
                if current.state is DiscordTaskState.STOPPING:
                    await self._finish_stopping(current)
                    return
                if current.state is not DiscordTaskState.RECOVERING:
                    return
                current = self._transition(current, DiscordTaskState.STARTING)
            else:
                current = self._store.get(task_id)
            if current.state is DiscordTaskState.STOPPING:
                await self._finish_stopping(current)
                return
            if current.state is not DiscordTaskState.STARTING:
                return
            running = self._transition(current, DiscordTaskState.RUNNING)
            await self.render(running)
            reply = await self._agent.ask(
                spec.prompt,
                str(running.owner_id),
                running.execution_channel_id,
                source_message_id=spec.source_message_id,
                attachment_paths=spec.attachments.paths,
                origin_context=spec.origin_context,
                on_progress=self._presentation.progress_sink(task_id),
                execution=AgentExecutionContext(
                    channel_id=running.execution_channel_id,
                    trigger_event_id=running.trigger_event_id,
                    require_existing_session=spec.require_existing_session,
                ),
            )
            await self._prepare_delivery(task_id, reply)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Discord task execution failed task_id=%s error_type=%s",
                task_id,
                type(error).__name__,
            )
            await record_agent_failure(
                task_id=task_id,
                error=error,
                agent=self._agent,
                store=self._store,
                now=self._now,
                render=self.render,
            )
        finally:
            try:
                spec.attachments.cleanup()
            except Exception:
                logger.warning("Discord task input cleanup deferred task_id=%s", task_id)

    async def _create_card(self, task_id: str) -> None:
        current = self._store.get(task_id)
        if current.card_message_id is not None:
            return
        try:
            card_id = await self._presentation.create_card(current)
        except Exception:
            logger.warning("Discord task card creation failed task_id=%s", task_id)
            return
        if card_id is None:
            return
        attached = self._set_message_id(task_id, "card_message_id", card_id)
        await self.render(attached)

    async def _prepare_delivery(self, task_id: str, reply: AgentReply) -> None:
        current = self._store.get(task_id)
        if current.state is DiscordTaskState.STOPPING:
            await self._finish_stopping(current)
            return
        if current.state is not DiscordTaskState.RUNNING:
            return
        prepared = await self._presentation.prepare_reply(current, reply)
        self._delivery.put(task_id, prepared)
        current = self._store.get(task_id)
        if current.state is DiscordTaskState.STOPPING:
            self._delivery.discard(task_id)
            await self._finish_stopping(current)
            return
        leased = self._delivery.consume(task_id)
        if leased is None:
            raise AgentWorkspaceOrAttachmentError(
                "Discord reply attachments could not be prepared"
            )
        try:
            delivering = self._transition(current, DiscordTaskState.DELIVERING)
        except BaseException:
            self._delivery.restore(task_id, leased)
            self._delivery.discard(task_id)
            raise
        await self._send_delivery(delivering.task_id, leased)

    async def _send_delivery(
        self, task_id: str, reply: PreparedDiscordReply
    ) -> None:
        record = self._store.get(task_id)
        try:
            result_id = await self._delivery.send(record, reply)
        except asyncio.CancelledError:
            raise
        except DiscordTaskDeliveryError as error:
            await self._delivery_failed(task_id, error.definitive_non_delivery)
            return
        except Exception:
            await self._delivery_failed(task_id, False)
            return
        current = self._store.get(task_id)
        completed = persist_update(
            self._store,
            current,
            lambda record: transition(
                replace(record, result_message_id=result_id),
                DiscordTaskState.COMPLETED,
                self._now(),
            ),
        )
        await self.render(completed)

    async def _delivery_failed(self, task_id: str, definitive: bool) -> None:
        current = self._store.get(task_id)
        if current.state is not DiscordTaskState.DELIVERING:
            return
        failure = classify_delivery_failure(definitive_non_delivery=definitive)
        failed = self._transition(
            current,
            DiscordTaskState.DELIVERY_FAILED,
            failure=failure,
        )
        await self.render(failed)

    async def _finish_stopping(self, record: DiscordTaskRecord) -> None:
        if record.state is DiscordTaskState.STOPPING:
            await self.render(self._transition(record, DiscordTaskState.STOPPED))

    def _transition(
        self,
        current: DiscordTaskRecord,
        target: DiscordTaskState,
        *,
        failure: DiscordTaskFailure | None = None,
    ) -> DiscordTaskRecord:
        return persist_update(
            self._store,
            current,
            lambda record: transition(
                record,
                target,
                self._now(),
                failure=failure,
            ),
        )

    def _set_message_id(self, task_id: str, field: str, message_id: int) -> DiscordTaskRecord:
        for _ in range(4):
            current = self._store.get(task_id)
            existing = getattr(current, field)
            if existing is not None:
                return current
            try:
                return persist_update(
                    self._store,
                    current,
                    lambda record: replace(
                        record,
                        **{field: message_id, "updated_at": self._now()},
                    ),
                )
            except TaskRevisionConflict:
                continue
        return self._store.get(task_id)

    def _now(self) -> str:
        return as_timestamp(self._timestamp())
