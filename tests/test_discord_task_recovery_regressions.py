import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentChannelCapabilities
from study_discord_agent.agent_errors import AgentTurnTimedOut
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_service import DiscordTaskActionUnavailable
from tests.test_discord_task_service_fixtures import (
    TrackingAttachments,
    access,
    make_harness,
    request,
    stored_record,
    wait_for_state,
)

RESUMABLE_FAILURE = DiscordTaskFailure(
    category=DiscordTaskFailureCategory.TIMEOUT,
    summary="The agent timed out. Partial work and the agent session were kept.",
    retry_mode=DiscordTaskRetryMode.CONTINUE_SESSION,
)


@pytest.mark.asyncio
async def test_retry_waits_for_the_live_failed_runner_before_reserving_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    harness.agent.ask_errors[10] = AgentTurnTimedOut("timeout")
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)
    failed_render_entered = asyncio.Event()
    failed_render_release = asyncio.Event()
    original_render = harness.presentation.render_card

    async def block_failed_render(record: DiscordTaskRecord) -> None:
        await original_render(record)
        if record.state is DiscordTaskState.TIMED_OUT:
            failed_render_entered.set()
            await failed_render_release.wait()

    monkeypatch.setattr(harness.presentation, "render_card", block_failed_render)

    task = await harness.service.start(request())
    await failed_render_entered.wait()
    failed = harness.store.get(task.task_id)
    assert failed.state is DiscordTaskState.TIMED_OUT
    assert failed.failure is not None
    assert failed.failure.retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION

    harness.agent.ask_errors.clear()
    harness.agent.start_release = asyncio.Event()
    retry = asyncio.create_task(
        harness.service.retry(task.task_id, access(), interaction_id=1_100)
    )
    await asyncio.sleep(0)

    assert not retry.done()
    assert harness.store.get(task.task_id).state is DiscordTaskState.TIMED_OUT

    failed_render_release.set()
    retrying = await retry
    assert retrying.state is DiscordTaskState.RECOVERING
    await harness.agent.start_entered.wait()
    harness.agent.start_release.set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.COMPLETED)
    await harness.service.close()


@pytest.mark.asyncio
async def test_retry_rejects_stale_same_session_capability_before_reservation(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    record = stored_record(
        "00000000000000000000000000000001",
        DiscordTaskState.TIMED_OUT,
        failure=RESUMABLE_FAILURE,
    )
    harness.store.create(record)
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, False, False, False)

    try:
        with pytest.raises(DiscordTaskActionUnavailable, match="session"):
            await harness.service.retry(record.task_id, access(), interaction_id=1_101)

        assert harness.store.get(record.task_id).state is DiscordTaskState.TIMED_OUT
        assert not harness.agent.ask_calls
    finally:
        await harness.service.close()


@pytest.mark.asyncio
async def test_continue_controls_and_action_require_a_fresh_idle_saved_session(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    parent = stored_record(
        "00000000000000000000000000000001", DiscordTaskState.COMPLETED
    )
    harness.store.create(parent)
    inputs = TrackingAttachments()
    continuation = request(
        trigger_event_id=410,
        attachments=inputs,
        source_kind=DiscordTaskSourceKind.CONTINUATION,
    )

    try:
        controls = await harness.service.resolve_controls(parent.task_id, access())
        assert not controls.continuable
        with pytest.raises(DiscordTaskActionUnavailable, match="session"):
            await harness.service.continue_task(
                parent.task_id,
                access(),
                continuation,
                interaction_id=1_102,
            )

        assert len(harness.store.records()) == 1
        assert not harness.presentation.create_calls
        assert not harness.agent.ask_calls
        assert inputs.cleanup_calls == 1
    finally:
        await harness.service.close()


@pytest.mark.asyncio
async def test_latest_continue_compares_timezone_aware_creation_instants(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    earlier = replace(
        stored_record(
            "00000000000000000000000000000001", DiscordTaskState.COMPLETED
        ),
        created_at="2026-07-17T13:30:00+02:00",
    )
    later = replace(
        stored_record(
            "00000000000000000000000000000002", DiscordTaskState.COMPLETED
        ),
        created_at="2026-07-17T12:00:00+00:00",
    )
    harness.store.create(earlier)
    harness.store.create(later)
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)

    old_controls = await harness.service.resolve_controls(earlier.task_id, access())
    latest_controls = await harness.service.resolve_controls(later.task_id, access())

    assert not old_controls.continuable
    assert latest_controls.continuable
    await harness.service.close()
