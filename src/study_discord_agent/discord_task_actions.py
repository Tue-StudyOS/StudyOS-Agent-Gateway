from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from study_discord_agent.agent import AgentGateway
from study_discord_agent.codex_app_server_runtime import SteerResult
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAction,
    authorize,
)
from study_discord_agent.discord_task_execution import AgentRunSpec
from study_discord_agent.discord_task_failures import classify_delivery_failure
from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskState,
    claim_interruption,
    transition,
)
from study_discord_agent.discord_task_queries import DiscordTaskQueries
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from study_discord_agent.discord_task_runtime import DiscordTaskRuntime
from study_discord_agent.discord_task_service_errors import DiscordTaskActionUnavailable
from study_discord_agent.discord_task_service_state import (
    BoundedClaims,
    as_timestamp,
    new_record,
    persist_link,
    persist_update,
)
from study_discord_agent.discord_task_store import (
    DiscordTaskStore,
    TaskRevisionConflict,
)

GENERIC_RESUME_PROMPT = (
    "Continue the saved task from its existing session. Review the work already present "
    "and finish the task safely without asking for the original prompt."
)


class DiscordTaskActions:
    def __init__(
        self,
        *,
        agent: AgentGateway,
        store: DiscordTaskStore,
        runtime: DiscordTaskRuntime,
        queries: DiscordTaskQueries,
        interactions: BoundedClaims,
        triggers: BoundedClaims,
        clock: Callable[[], datetime],
        task_id_factory: Callable[[], str],
    ) -> None:
        self._agent = agent
        self._store = store
        self._runtime = runtime
        self._queries = queries
        self._interactions = interactions
        self._triggers = triggers
        self._clock = clock
        self._task_id_factory = task_id_factory

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        try:
            record = self._queries.status(task_id, access)
            authorize(record, DiscordTaskAction.STEER, access)
            duplicate = self._interactions.existing(interaction_id)
            if duplicate is not None:
                return self._store.get(duplicate)
            if record.state is not DiscordTaskState.RUNNING:
                raise DiscordTaskActionUnavailable("Only a running task can be steered.")
            self._interactions.remember(interaction_id, task_id)
            try:
                capabilities = await self._agent.channel_capabilities(record.execution_channel_id)
            except Exception as error:
                raise DiscordTaskActionUnavailable("This task is not steerable now.") from error
            record = self._store.get(task_id)
            authorize(record, DiscordTaskAction.STEER, access)
            if record.state is not DiscordTaskState.RUNNING or not capabilities.steering:
                raise DiscordTaskActionUnavailable("This task is not steerable now.")
            result = await self._agent.steer(
                prompt=request.prompt,
                user=str(record.owner_id),
                channel_id=record.execution_channel_id,
                source_message_id=request.source_message_id,
                attachment_paths=request.attachments.paths,
                origin_context=request.origin_context,
            )
            if result is not SteerResult.STEERED:
                raise DiscordTaskActionUnavailable("This task is not steerable now.")
            return self._store.get(task_id)
        finally:
            request.attachments.cleanup()

    async def stop(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        record = self._queries.status(task_id, access)
        authorize(record, DiscordTaskAction.STOP, access)
        duplicate = self._interactions.existing(interaction_id)
        if duplicate is not None:
            return self._store.get(duplicate)
        if record.state is DiscordTaskState.STOPPING:
            self._interactions.remember(interaction_id, task_id)
            await self._agent.interrupt(record.execution_channel_id)
            return self._store.get(task_id)
        if record.state not in {
            DiscordTaskState.RECOVERING,
            DiscordTaskState.STARTING,
            DiscordTaskState.RUNNING,
        }:
            raise DiscordTaskActionUnavailable("This task can no longer be stopped.")

        def claim_stop(current: DiscordTaskRecord) -> DiscordTaskRecord:
            claimed = claim_interruption(current, DiscordTaskInterruptionCause.USER_STOP)
            return transition(claimed, DiscordTaskState.STOPPING, self._now())

        try:
            stopping = persist_update(self._store, record, claim_stop)
        except TaskRevisionConflict as error:
            raise DiscordTaskActionUnavailable(
                "This task changed before Stop was accepted."
            ) from error
        self._interactions.remember(interaction_id, task_id)
        await self._agent.interrupt(stopping.execution_channel_id)
        await self._runtime.render(stopping)
        return self._store.get(task_id)

    async def retry(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        record = self._queries.status(task_id, access)
        authorize(record, DiscordTaskAction.RETRY, access)
        duplicate = self._interactions.existing(interaction_id)
        if duplicate is not None:
            return self._store.get(duplicate)
        if record.failure is None:
            raise DiscordTaskActionUnavailable("This task has no safe retry.")
        if record.failure.retry_mode is not DiscordTaskRetryMode.RETRY_DELIVERY:
            self._queries.validate_session_retry(record)
        await self._runtime.wait_idle(task_id)
        record = self._queries.status(task_id, access)
        authorize(record, DiscordTaskAction.RETRY, access)
        duplicate = self._interactions.existing(interaction_id)
        if duplicate is not None:
            return self._store.get(duplicate)
        if record.failure is None:
            raise DiscordTaskActionUnavailable("This task has no safe retry.")
        self._runtime.ensure_open()
        if record.failure.retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY:
            return await self._retry_delivery(record, interaction_id)
        self._queries.validate_session_retry(record)
        await self._queries.require_idle_saved_session(record)
        record = self._queries.status(task_id, access)
        authorize(record, DiscordTaskAction.RETRY, access)
        duplicate = self._interactions.existing(interaction_id)
        if duplicate is not None:
            return self._store.get(duplicate)
        self._queries.validate_session_retry(record)
        self._runtime.ensure_open()
        try:
            recovering = persist_update(
                self._store,
                record,
                lambda current: transition(current, DiscordTaskState.RECOVERING, self._now()),
            )
        except (TaskRevisionConflict, ValueError) as error:
            raise DiscordTaskActionUnavailable(
                "This task cannot recover while another task is active."
            ) from error
        self._interactions.remember(interaction_id, task_id)
        self._runtime.spawn_agent(task_id, AgentRunSpec.for_recovery(GENERIC_RESUME_PROMPT))
        await self._runtime.render(recovering)
        return recovering

    async def continue_task(
        self,
        parent_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        accepted = False
        try:
            parent = self._queries.status(parent_id, access)
            authorize(parent, DiscordTaskAction.CONTINUE, access)
            duplicate = self._interactions.existing(interaction_id)
            if duplicate is not None:
                return self._store.get(duplicate)
            self._queries.validate_continuation(parent, request)
            await self._queries.require_idle_saved_session(parent)
            parent = self._queries.status(parent_id, access)
            authorize(parent, DiscordTaskAction.CONTINUE, access)
            duplicate = self._interactions.existing(interaction_id)
            if duplicate is not None:
                return self._store.get(duplicate)
            self._queries.validate_continuation(parent, request)
            self._runtime.ensure_open()
            task_id = request.task_id or self._task_id_factory()
            child = new_record(
                request,
                task_id,
                self._clock(),
                continued_from=parent,
            )
            linked_parent, linked_child = persist_link(self._store, parent, child)
            self._interactions.remember(interaction_id, linked_child.task_id)
            self._triggers.remember(request.trigger_event_id, linked_child.task_id)
            self._runtime.spawn_agent(linked_child.task_id, AgentRunSpec.from_request(request))
            accepted = True
            await self._runtime.render(linked_parent)
            return linked_child
        finally:
            if not accepted:
                request.attachments.cleanup()

    async def forget(self, task_id: str, access: DiscordTaskAccess, interaction_id: int) -> None:
        if self._interactions.existing(interaction_id) is not None:
            return
        record = self._queries.status(task_id, access)
        authorize(record, DiscordTaskAction.FORGET, access)
        if record.state in ACTIVE_STATES:
            raise DiscordTaskActionUnavailable("An active task cannot be forgotten.")
        try:
            neighbors = self._store.forget(task_id, record.revision)
        except ValueError as error:
            raise DiscordTaskActionUnavailable("An active task cannot be forgotten.") from error
        self._interactions.remember(interaction_id, task_id)
        self._runtime.discard(task_id)
        for neighbor in neighbors:
            await self._runtime.render(neighbor)

    async def _retry_delivery(
        self, record: DiscordTaskRecord, interaction_id: int
    ) -> DiscordTaskRecord:
        reply: PreparedDiscordReply | None = self._runtime.consume_delivery(record.task_id)
        if reply is None:
            unavailable = persist_update(
                self._store,
                record,
                lambda current: replace(
                    current,
                    failure=classify_delivery_failure(definitive_non_delivery=False),
                    updated_at=self._now(),
                ),
            )
            self._interactions.remember(interaction_id, record.task_id)
            await self._runtime.render(unavailable)
            return unavailable
        try:
            delivering = persist_update(
                self._store,
                record,
                lambda current: transition(current, DiscordTaskState.DELIVERING, self._now()),
            )
        except BaseException:
            self._runtime.restore_delivery(record.task_id, reply)
            raise
        self._interactions.remember(interaction_id, record.task_id)
        self._runtime.spawn_delivery(record.task_id, reply)
        await self._runtime.render(delivering)
        return delivering

    def _now(self) -> str:
        return as_timestamp(self._clock())
