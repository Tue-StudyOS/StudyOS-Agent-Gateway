from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from study_discord_agent.agent import AgentGateway
from study_discord_agent.discord_delivery_cache import DiscordDeliveryCache
from study_discord_agent.discord_task_actions import (
    GENERIC_RESUME_PROMPT as GENERIC_RESUME_PROMPT,
)
from study_discord_agent.discord_task_actions import DiscordTaskActions
from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_delivery import (
    DiscordTaskDelivery,
    DiscordTaskPresentation,
)
from study_discord_agent.discord_task_inputs import retry_pending_staging_cleanups
from study_discord_agent.discord_task_model import DiscordTaskRecord
from study_discord_agent.discord_task_queries import DiscordTaskQueries
from study_discord_agent.discord_task_reconciliation import DiscordTaskReconciler
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from study_discord_agent.discord_task_runtime import AgentRunSpec, DiscordTaskRuntime
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskActionUnavailable as DiscordTaskActionUnavailable,
)
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskChannelBusy as DiscordTaskChannelBusy,
)
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskControlState as DiscordTaskControlState,
)
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskServiceClosed as DiscordTaskServiceClosed,
)
from study_discord_agent.discord_task_service_state import (
    BoundedClaims,
    new_record,
    persist_create,
)
from study_discord_agent.discord_task_store import DiscordTaskStore


class DiscordTaskService:
    def __init__(
        self,
        *,
        agent: AgentGateway,
        store: DiscordTaskStore,
        presentation: DiscordTaskPresentation,
        delivery_cache: DiscordDeliveryCache,
        allowed_artifact_roots: tuple[Path, ...],
        max_artifact_bytes: int,
        clock: Callable[[], datetime] | None = None,
        task_id_factory: Callable[[], str] | None = None,
        claim_limit: int = 2_048,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task_id_factory = task_id_factory or (lambda: uuid4().hex)
        self._triggers = BoundedClaims(claim_limit)
        interactions = BoundedClaims(claim_limit)
        delivery = DiscordTaskDelivery(
            delivery_cache,
            presentation,
            allowed_roots=allowed_artifact_roots,
            max_bytes=max_artifact_bytes,
        )
        self._runtime = DiscordTaskRuntime(
            agent=agent,
            store=store,
            presentation=presentation,
            delivery=delivery,
            timestamp=self._clock,
        )
        self._queries = DiscordTaskQueries(store, agent)
        self._actions = DiscordTaskActions(
            agent=agent,
            store=store,
            runtime=self._runtime,
            queries=self._queries,
            interactions=interactions,
            triggers=self._triggers,
            clock=self._clock,
            task_id_factory=self._task_id_factory,
        )
        self._reconciler = DiscordTaskReconciler(
            agent=agent,
            store=store,
            runtime=self._runtime,
            clock=self._clock,
        )
        self._closed = False
        self._close_complete = False

    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord:
        try:
            self._ensure_open()
        except DiscordTaskServiceClosed:
            request.attachments.cleanup()
            raise
        claimed_task_id = self._triggers.existing(request.trigger_event_id)
        if claimed_task_id is not None:
            request.attachments.cleanup()
            try:
                return self._store.get(claimed_task_id)
            except KeyError as error:
                raise DiscordTaskActionUnavailable(
                    "This Discord request was already handled."
                ) from error
        existing = self._by_trigger(request.trigger_event_id)
        if existing is not None:
            request.attachments.cleanup()
            return existing
        try:
            record = new_record(request, self._task_id_factory(), self._clock())
        except BaseException:
            request.attachments.cleanup()
            raise
        try:
            persist_create(self._store, record)
        except ValueError as error:
            request.attachments.cleanup()
            raise DiscordTaskChannelBusy(
                "This channel already has an active task."
            ) from error
        except BaseException:
            request.attachments.cleanup()
            raise
        self._triggers.remember(request.trigger_event_id, record.task_id)
        self._runtime.spawn_agent(record.task_id, AgentRunSpec.from_request(request))
        return record

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        try:
            self._ensure_open()
        except DiscordTaskServiceClosed:
            request.attachments.cleanup()
            raise
        return await self._actions.steer(task_id, access, request, interaction_id)

    async def stop(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        self._ensure_open()
        return await self._actions.stop(task_id, access, interaction_id)

    async def retry(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        self._ensure_open()
        return await self._actions.retry(task_id, access, interaction_id)

    async def continue_task(
        self,
        parent_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        try:
            self._ensure_open()
        except DiscordTaskServiceClosed:
            request.attachments.cleanup()
            raise
        return await self._actions.continue_task(
            parent_id,
            access,
            request,
            interaction_id,
        )

    async def forget(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> None:
        self._ensure_open()
        await self._actions.forget(task_id, access, interaction_id)

    def status(self, task_id: str, access: DiscordTaskAccess) -> DiscordTaskRecord:
        return self._queries.status(task_id, access)

    def active_task(self, execution_channel_id: int) -> DiscordTaskRecord | None:
        return self._queries.active_task(execution_channel_id)

    def list_tasks(
        self,
        access: DiscordTaskAccess,
        scope: str,
        state: str,
        current_channel_id: int,
    ) -> tuple[DiscordTaskRecord, ...]:
        return self._queries.list_tasks(access, scope, state, current_channel_id)

    async def resolve_controls(
        self, task_id: str, access: DiscordTaskAccess
    ) -> DiscordTaskControlState:
        return await self._queries.resolve_controls(task_id, access)

    async def reconcile_startup(self) -> tuple[DiscordTaskRecord, ...]:
        self._ensure_open()
        return await self._reconciler.reconcile()

    async def close(self) -> None:
        if self._close_complete:
            return
        self._closed = True
        error: BaseException | None = None
        try:
            await self._runtime.close()
        except BaseException as caught:
            error = caught
        try:
            retry_pending_staging_cleanups()
        except BaseException as caught:
            error = error or caught
        if error is not None:
            raise error
        self._close_complete = True

    def _by_trigger(self, trigger_event_id: int) -> DiscordTaskRecord | None:
        return next(
            (
                record
                for record in self._store.records()
                if record.trigger_event_id == trigger_event_id
            ),
            None,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise DiscordTaskServiceClosed("Discord task service is closed")
