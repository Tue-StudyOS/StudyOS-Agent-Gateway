import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.agent import AgentChannelCapabilities, AgentExecutionContext
from study_discord_agent.codex_app_server_runtime import SteerResult
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskInterruptionCause,
    DiscordTaskState,
)
from study_discord_agent.discord_task_request import DiscordTaskSteerRequest
from study_discord_agent.discord_task_service import (
    DiscordTaskActionUnavailable,
    DiscordTaskChannelBusy,
)
from tests.test_discord_task_service_fixtures import (
    TrackingAttachments,
    access,
    make_harness,
    request,
    wait_for_state,
    wait_until,
)


def test_request_rejects_blank_prompt_and_invalid_source_label() -> None:
    valid = request()

    with pytest.raises(ValueError, match="prompt"):
        replace(valid, prompt="  ")
    with pytest.raises(ValueError, match="source_label"):
        replace(valid, source_label="")


@pytest.mark.asyncio
async def test_start_reserves_before_io_deduplicates_trigger_and_parallelizes_channels(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    card_release = harness.presentation.block_card(10)
    first_inputs = TrackingAttachments()

    first = await harness.service.start(request(attachments=first_inputs))
    await wait_until(lambda: 10 in harness.presentation.create_entered)
    await harness.presentation.create_entered[10].wait()

    assert harness.store.get(first.task_id).state is DiscordTaskState.STARTING
    duplicate_inputs = TrackingAttachments()
    duplicate = await harness.service.start(
        request(attachments=duplicate_inputs)
    )
    assert duplicate.task_id == first.task_id
    assert duplicate_inputs.cleanup_calls == 1

    rejected_inputs = TrackingAttachments()
    with pytest.raises(DiscordTaskChannelBusy):
        await harness.service.start(
            request(trigger_event_id=101, attachments=rejected_inputs)
        )
    assert rejected_inputs.cleanup_calls == 1

    second_release = harness.agent.block_channel(11)
    second = await harness.service.start(
        request(channel_id=11, trigger_event_id=201)
    )
    await wait_for_state(harness.store, second.task_id, DiscordTaskState.RUNNING)
    assert not card_release.is_set()

    first_release = harness.agent.block_channel(10)
    card_release.set()
    await wait_for_state(harness.store, first.task_id, DiscordTaskState.RUNNING)
    assert harness.store.get(first.task_id).card_message_id is not None
    first_release.set()
    second_release.set()
    await wait_for_state(harness.store, second.task_id, DiscordTaskState.COMPLETED)
    await wait_for_state(harness.store, first.task_id, DiscordTaskState.COMPLETED)
    await harness.service.close()


@pytest.mark.asyncio
async def test_card_id_is_persisted_before_agent_and_missing_card_is_best_effort(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    harness.agent.block_channel(10)

    task = await harness.service.start(request())
    await harness.agent.ask_started.setdefault(10, asyncio.Event()).wait()

    running = harness.store.get(task.task_id)
    assert running.card_message_id is not None
    execution = cast(AgentExecutionContext, harness.agent.ask_calls[0]["execution"])
    assert execution is not None
    assert execution.channel_id == 10
    assert execution.trigger_event_id == 100

    harness.agent.ask_release[10].set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.COMPLETED)
    await harness.service.close()

    missing = make_harness(tmp_path / "missing")
    missing.presentation.missing_card_channels.add(10)
    missing.agent.block_channel(10)
    task = await missing.service.start(request())
    await wait_for_state(missing.store, task.task_id, DiscordTaskState.RUNNING)
    assert missing.store.get(task.task_id).card_message_id is None
    missing.agent.ask_release[10].set()
    await wait_for_state(missing.store, task.task_id, DiscordTaskState.COMPLETED)
    await missing.service.close()


@pytest.mark.asyncio
async def test_start_owns_inputs_through_runner_finally_and_card_errors_do_not_fail_agent(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    harness.presentation.raise_create_channels.add(10)
    release = harness.agent.block_channel(10)
    inputs = TrackingAttachments()

    task = await harness.service.start(request(attachments=inputs))
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)
    assert inputs.cleanup_calls == 0

    release.set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.COMPLETED)
    await wait_until(lambda: inputs.cleanup_calls == 1)
    await harness.service.close()


@pytest.mark.asyncio
async def test_steer_requires_running_fresh_capability_and_always_cleans_inputs(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)
    harness.agent.capabilities[10] = AgentChannelCapabilities(True, False, True, True)
    inputs = TrackingAttachments()
    steer_request = DiscordTaskSteerRequest(
        prompt="use this context",
        source_message_id=123,
        attachments=cast(StagedDiscordAttachments, inputs),
        origin_context=None,
    )

    steered = await harness.service.steer(
        task.task_id, access(), steer_request, interaction_id=500
    )
    duplicate = await harness.service.steer(
        task.task_id, access(), steer_request, interaction_id=500
    )

    assert steered.task_id == duplicate.task_id
    assert len(harness.agent.steer_calls) == 1
    assert inputs.cleanup_calls == 2

    harness.agent.steer_result = SteerResult.NO_ACTIVE_TURN
    with pytest.raises(DiscordTaskActionUnavailable, match="steer"):
        await harness.service.steer(
            task.task_id,
            access(),
            DiscordTaskSteerRequest(
                "more",
                None,
                cast(StagedDiscordAttachments, TrackingAttachments()),
                None,
            ),
            interaction_id=501,
        )

    release.set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.COMPLETED)
    await harness.service.close()


@pytest.mark.asyncio
async def test_stop_claims_user_stop_before_interrupt_and_completion_cannot_win(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    harness.agent.interrupt_result = True
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)

    stopping = await harness.service.stop(task.task_id, access(), interaction_id=600)

    assert stopping.state is DiscordTaskState.STOPPING
    assert stopping.interruption_cause is not None
    assert stopping.interruption_cause.value == "user_stop"
    assert harness.agent.interrupt_calls == [10]
    release.set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.STOPPED)
    assert not harness.presentation.deliver_calls
    await harness.service.close()


@pytest.mark.asyncio
async def test_new_stop_interaction_retries_failed_interrupt_without_reclaiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)
    interrupt_calls: list[int] = []

    async def interrupt_once_failed(channel_id: int) -> bool:
        interrupt_calls.append(channel_id)
        if len(interrupt_calls) == 1:
            raise RuntimeError("interrupt transport failed")
        return True

    monkeypatch.setattr(harness.agent, "interrupt", interrupt_once_failed)

    try:
        with pytest.raises(RuntimeError, match="interrupt transport failed"):
            await harness.service.stop(task.task_id, access(), interaction_id=601)
        claimed = harness.store.get(task.task_id)
        duplicate = await harness.service.stop(
            task.task_id, access(), interaction_id=601
        )
        retried = await harness.service.stop(task.task_id, access(), interaction_id=602)
        second_duplicate = await harness.service.stop(
            task.task_id, access(), interaction_id=602
        )
        revision_after_actions = harness.store.get(task.task_id).revision
    finally:
        release.set()
        await wait_for_state(harness.store, task.task_id, DiscordTaskState.STOPPED)
        await harness.service.close()

    assert claimed.state is DiscordTaskState.STOPPING
    assert claimed.interruption_cause is DiscordTaskInterruptionCause.USER_STOP
    assert duplicate == claimed
    assert retried == claimed
    assert second_duplicate == claimed
    assert interrupt_calls == [10, 10]
    assert revision_after_actions == claimed.revision
    assert retried.interruption_cause is DiscordTaskInterruptionCause.USER_STOP


@pytest.mark.asyncio
async def test_close_cancels_without_claiming_stop_and_retries_staging_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    inputs = TrackingAttachments()
    cleanup_retries = 0

    def retry_cleanup() -> None:
        nonlocal cleanup_retries
        cleanup_retries += 1

    monkeypatch.setattr(
        "study_discord_agent.discord_task_service.retry_pending_staging_cleanups",
        retry_cleanup,
    )
    task = await harness.service.start(request(attachments=inputs))
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)

    await harness.service.close()

    record = harness.store.get(task.task_id)
    assert record.state is DiscordTaskState.RUNNING
    assert record.interruption_cause is None
    assert inputs.cleanup_calls == 1
    assert cleanup_retries == 1
    assert not release.is_set()
    assert harness.presentation.close_calls == 1
