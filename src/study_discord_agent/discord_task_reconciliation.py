from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from study_discord_agent.agent import AgentChannelCapabilities, AgentGateway
from study_discord_agent.agent_errors import AgentRuntimeDisconnected
from study_discord_agent.discord_task_failures import classify_agent_failure
from study_discord_agent.discord_task_model import (
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskState,
)
from study_discord_agent.discord_task_runtime import DiscordTaskRuntime
from study_discord_agent.discord_task_service_state import as_timestamp, persist_update
from study_discord_agent.discord_task_store import DiscordTaskStore


class DiscordTaskReconciler:
    def __init__(
        self,
        *,
        agent: AgentGateway,
        store: DiscordTaskStore,
        runtime: DiscordTaskRuntime,
        clock: Callable[[], datetime],
    ) -> None:
        self._agent = agent
        self._store = store
        self._runtime = runtime
        self._clock = clock

    async def reconcile(self) -> tuple[DiscordTaskRecord, ...]:
        changed = self._store.reconcile_startup(self._clock())
        reconciled: list[DiscordTaskRecord] = []
        for record in changed:
            if (
                record.state is DiscordTaskState.INTERRUPTED
                and record.interruption_cause
                is DiscordTaskInterruptionCause.GATEWAY_RESTART
            ):
                record = await self._enrich_restart(record)
            await self._runtime.render(record)
            reconciled.append(record)
        return tuple(reconciled)

    async def _enrich_restart(self, record: DiscordTaskRecord) -> DiscordTaskRecord:
        try:
            capabilities = await self._agent.channel_capabilities(
                record.execution_channel_id
            )
        except Exception:
            capabilities = AgentChannelCapabilities(False, False, False, False)
        failure = classify_agent_failure(
            AgentRuntimeDisconnected("gateway restart"),
            persisted_session=capabilities.persisted_session,
            active_turn=capabilities.active_turn,
        )
        current = self._store.get(record.task_id)
        return persist_update(
            self._store,
            current,
            lambda latest: replace(
                latest,
                failure=failure,
                updated_at=as_timestamp(self._clock()),
            ),
        )
