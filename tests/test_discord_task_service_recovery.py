import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentChannelCapabilities
from study_discord_agent.agent_errors import AgentRuntimeDisconnected, AgentTurnTimedOut
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskInterruptionCause,
    DiscordTaskRetryMode,
    DiscordTaskState,
)
from study_discord_agent.discord_task_service import GENERIC_RESUME_PROMPT
from tests.test_discord_task_service_fixtures import (
    NOW,
    access,
    make_harness,
    request,
    stored_record,
    wait_for_state,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "cause"),
    [
        (AgentTurnTimedOut("secret"), DiscordTaskState.TIMED_OUT, "timeout"),
        (
            AgentRuntimeDisconnected("secret"),
            DiscordTaskState.INTERRUPTED,
            "runtime_exit",
        ),
    ],
)
async def test_agent_interruptions_claim_first_cause_and_use_fresh_capabilities(
    tmp_path: Path,
    error: Exception,
    state: DiscordTaskState,
    cause: str,
) -> None:
    harness = make_harness(tmp_path)
    harness.agent.ask_errors[10] = error
    harness.agent.capabilities[10] = AgentChannelCapabilities(
        steering=False,
        resumable=True,
        persisted_session=True,
        active_turn=False,
    )

    task = await harness.service.start(request())
    failed = await wait_for_state(harness.store, task.task_id, state)

    assert failed.interruption_cause is DiscordTaskInterruptionCause(cause)
    assert failed.failure is not None
    assert failed.failure.retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION
    assert "secret" not in failed.failure.summary
    await harness.service.close()


@pytest.mark.asyncio
async def test_generic_retry_claims_recovering_reuses_id_and_never_replays_prompt(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    harness.agent.ask_errors[10] = AgentTurnTimedOut("timeout")
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)
    task = await harness.service.start(request(prompt="private original prompt"))
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.TIMED_OUT)
    harness.agent.ask_errors.clear()
    harness.agent.start_release = asyncio.Event()

    retrying = await harness.service.retry(task.task_id, access(), interaction_id=800)
    duplicate = await harness.service.retry(task.task_id, access(), interaction_id=800)

    assert retrying.task_id == task.task_id == duplicate.task_id
    assert retrying.state is DiscordTaskState.RECOVERING
    assert retrying.attempt == 2
    await harness.agent.start_entered.wait()
    assert harness.agent.start_calls == 1

    harness.agent.start_release.set()
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    assert completed.attempt == 2
    assert harness.agent.ask_calls[-1]["prompt"] == GENERIC_RESUME_PROMPT
    assert "private original prompt" not in GENERIC_RESUME_PROMPT
    await harness.service.close()


@pytest.mark.asyncio
async def test_startup_reconciliation_enriches_retry_from_live_runtime_state(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    active = stored_record(
        "00000000000000000000000000000001", DiscordTaskState.RUNNING
    )
    no_session = stored_record(
        "00000000000000000000000000000002",
        DiscordTaskState.RUNNING,
        channel_id=11,
    )
    harness.store.create(active)
    harness.store.create(no_session)
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)

    reconciled = await harness.service.reconcile_startup()

    by_id = {record.task_id: record for record in reconciled}
    resumable = by_id[active.task_id]
    unavailable = by_id[no_session.task_id]
    assert resumable.state is DiscordTaskState.INTERRUPTED
    assert resumable.failure is not None
    assert resumable.failure.retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION
    assert unavailable.failure is not None
    assert unavailable.failure.retry_mode is DiscordTaskRetryMode.NONE
    await harness.service.close()


@pytest.mark.asyncio
async def test_control_resolver_is_fresh_and_continue_requires_latest_unlinked_completed(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    older = stored_record(
        "00000000000000000000000000000001",
        DiscordTaskState.COMPLETED,
        created_at=NOW - timedelta(minutes=1),
    )
    latest = stored_record(
        "00000000000000000000000000000002", DiscordTaskState.COMPLETED
    )
    failed = stored_record(
        "00000000000000000000000000000003",
        DiscordTaskState.FAILED,
        channel_id=11,
        failure=DiscordTaskFailure(
            DiscordTaskFailureCategory.INTERNAL,
            "The task failed safely.",
            DiscordTaskRetryMode.CONTINUE_SESSION,
        ),
    )
    for record in (older, latest, failed):
        harness.store.create(record)
    harness.agent.capabilities[11] = AgentChannelCapabilities(False, True, True, False)

    old_controls = await harness.service.resolve_controls(older.task_id, access())
    latest_controls = await harness.service.resolve_controls(latest.task_id, access())
    retry_controls = await harness.service.resolve_controls(
        failed.task_id, access(channel_id=11)
    )

    assert not old_controls.continuable
    assert latest_controls.continuable
    assert retry_controls.resumable
    harness.agent.capabilities[11] = AgentChannelCapabilities(False, False, False, False)
    refreshed = await harness.service.resolve_controls(
        failed.task_id, access(channel_id=11)
    )
    assert not refreshed.resumable
    await harness.service.close()


@pytest.mark.asyncio
async def test_user_stop_beats_timeout_and_control_steering_is_refreshed(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    harness.agent.ask_errors[10] = AgentTurnTimedOut("late timeout")
    harness.agent.capabilities[10] = AgentChannelCapabilities(True, False, True, True)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)

    controls = await harness.service.resolve_controls(task.task_id, access())
    assert controls.steering
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, False, True, True)
    assert not (await harness.service.resolve_controls(task.task_id, access())).steering

    await harness.service.stop(task.task_id, access(), interaction_id=801)
    release.set()
    stopped = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.STOPPED
    )
    assert stopped.interruption_cause is DiscordTaskInterruptionCause.USER_STOP
    await harness.service.close()
