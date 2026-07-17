from collections.abc import Awaitable, Callable
from dataclasses import replace

from study_discord_agent.agent import AgentGateway
from study_discord_agent.agent_errors import AgentRuntimeDisconnected, AgentTurnTimedOut
from study_discord_agent.discord_task_failures import classify_agent_failure
from study_discord_agent.discord_task_model import (
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskState,
    claim_interruption,
    transition,
)
from study_discord_agent.discord_task_service_state import persist_update
from study_discord_agent.discord_task_store import DiscordTaskStore, TaskRevisionConflict


async def record_agent_failure(
    *,
    task_id: str,
    error: Exception,
    agent: AgentGateway,
    store: DiscordTaskStore,
    now: Callable[[], str],
    render: Callable[[DiscordTaskRecord], Awaitable[None]],
) -> None:
    current = store.get(task_id)
    if current.state is DiscordTaskState.STOPPING:
        await _finish_stopping(current, store, now, render)
        return
    target, cause = _failure_target(current, error)
    if target is None:
        return
    failure = classify_agent_failure(error, persisted_session=False, active_turn=False)

    def fail(record: DiscordTaskRecord) -> DiscordTaskRecord:
        claimed = claim_interruption(record, cause) if cause is not None else record
        return transition(claimed, target, now(), failure=failure)

    try:
        failed = persist_update(store, current, fail)
    except TaskRevisionConflict:
        latest = store.get(task_id)
        if latest.state is DiscordTaskState.STOPPING:
            await _finish_stopping(latest, store, now, render)
        return
    if cause is not None:
        try:
            capabilities = await agent.channel_capabilities(failed.execution_channel_id)
        except Exception:
            capabilities = None
        if capabilities is not None:
            enriched = classify_agent_failure(
                error,
                persisted_session=capabilities.persisted_session,
                active_turn=capabilities.active_turn,
            )
            latest = store.get(task_id)
            if latest.revision == failed.revision:
                failed = persist_update(
                    store,
                    latest,
                    lambda record: replace(
                        record,
                        failure=enriched,
                        updated_at=now(),
                    ),
                )
    await render(failed)


async def _finish_stopping(
    record: DiscordTaskRecord,
    store: DiscordTaskStore,
    now: Callable[[], str],
    render: Callable[[DiscordTaskRecord], Awaitable[None]],
) -> None:
    stopped = persist_update(
        store,
        record,
        lambda current: transition(current, DiscordTaskState.STOPPED, now()),
    )
    await render(stopped)


def _failure_target(
    record: DiscordTaskRecord, error: Exception
) -> tuple[DiscordTaskState | None, DiscordTaskInterruptionCause | None]:
    if record.state in {DiscordTaskState.RECOVERING, DiscordTaskState.STARTING}:
        return DiscordTaskState.FAILED, None
    if record.state is not DiscordTaskState.RUNNING:
        return None, None
    if isinstance(error, AgentTurnTimedOut):
        return DiscordTaskState.TIMED_OUT, DiscordTaskInterruptionCause.TIMEOUT
    if isinstance(error, AgentRuntimeDisconnected):
        return DiscordTaskState.INTERRUPTED, DiscordTaskInterruptionCause.RUNTIME_EXIT
    return DiscordTaskState.FAILED, None
