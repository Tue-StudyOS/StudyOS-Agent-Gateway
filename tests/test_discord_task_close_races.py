import asyncio
from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.agent import AgentChannelCapabilities, AgentReply
from study_discord_agent.discord_task_delivery import DiscordTaskDeliveryError
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_request import DiscordTaskSteerRequest
from study_discord_agent.discord_task_runners import DiscordTaskRunners
from study_discord_agent.discord_task_service import DiscordTaskServiceClosed
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
async def test_close_wins_generic_retry_blocked_on_session_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    record = stored_record(
        "00000000000000000000000000000001",
        DiscordTaskState.TIMED_OUT,
        failure=RESUMABLE_FAILURE,
    )
    harness.store.create(record)
    capability_entered = asyncio.Event()
    capability_release = asyncio.Event()

    async def blocked_capability(_channel_id: int) -> AgentChannelCapabilities:
        capability_entered.set()
        await capability_release.wait()
        return AgentChannelCapabilities(False, True, True, False)

    monkeypatch.setattr(harness.agent, "channel_capabilities", blocked_capability)
    retry = asyncio.create_task(
        harness.service.retry(record.task_id, access(), interaction_id=1_300)
    )
    await capability_entered.wait()

    await harness.service.close()
    capability_release.set()
    result = (await asyncio.gather(retry, return_exceptions=True))[0]
    await asyncio.sleep(0)

    assert isinstance(result, DiscordTaskServiceClosed)
    assert harness.store.get(record.task_id) == record
    assert harness.agent.start_calls == 0
    assert not harness.agent.ask_calls


@pytest.mark.asyncio
async def test_close_wins_delivery_retry_blocked_on_live_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("close before retry")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_outcomes.append(
        DiscordTaskDeliveryError("not sent", definitive_non_delivery=True)
    )
    failed_render_entered = asyncio.Event()
    original_render = harness.presentation.render_card

    async def block_failed_render(record: DiscordTaskRecord) -> None:
        await original_render(record)
        if record.state is DiscordTaskState.DELIVERY_FAILED:
            failed_render_entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(harness.presentation, "render_card", block_failed_render)
    task = await harness.service.start(request())
    await failed_render_entered.wait()
    failed = harness.store.get(task.task_id)
    first_reply = harness.presentation.deliver_calls[0][1]
    retry = asyncio.create_task(
        harness.service.retry(task.task_id, access(), interaction_id=1_301)
    )
    await asyncio.sleep(0)

    await harness.service.close()
    result = (await asyncio.gather(retry, return_exceptions=True))[0]
    await asyncio.sleep(0)

    assert isinstance(result, DiscordTaskServiceClosed)
    assert harness.store.get(task.task_id) == failed
    assert len(harness.presentation.deliver_calls) == 1
    assert first_reply.delivery_lease is not None
    assert first_reply.delivery_lease.closed


@pytest.mark.asyncio
async def test_close_wins_continue_blocked_on_session_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    parent = stored_record(
        "00000000000000000000000000000010", DiscordTaskState.COMPLETED
    )
    harness.store.create(parent)
    capability_entered = asyncio.Event()
    capability_release = asyncio.Event()

    async def blocked_capability(_channel_id: int) -> AgentChannelCapabilities:
        capability_entered.set()
        await capability_release.wait()
        return AgentChannelCapabilities(False, True, True, False)

    monkeypatch.setattr(harness.agent, "channel_capabilities", blocked_capability)
    inputs = TrackingAttachments()
    continuation = request(
        trigger_event_id=410,
        attachments=inputs,
        source_kind=DiscordTaskSourceKind.CONTINUATION,
    )
    continued = asyncio.create_task(
        harness.service.continue_task(
            parent.task_id, access(), continuation, interaction_id=1_302
        )
    )
    await capability_entered.wait()

    await harness.service.close()
    capability_release.set()
    result = (await asyncio.gather(continued, return_exceptions=True))[0]
    for _ in range(10):
        await asyncio.sleep(0)

    assert isinstance(result, DiscordTaskServiceClosed)
    assert harness.store.records() == (parent,)
    assert inputs.cleanup_calls == 1
    assert not harness.presentation.create_calls
    assert not harness.agent.ask_calls


@pytest.mark.asyncio
async def test_runner_close_rejects_late_spawn_and_leaves_no_runner() -> None:
    runners = DiscordTaskRunners()
    live_entered = asyncio.Event()
    live_finished = asyncio.Event()

    async def live_runner() -> None:
        live_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            live_finished.set()

    runners.spawn("live", live_runner())
    await live_entered.wait()
    await runners.close()

    assert live_finished.is_set()
    assert not _live_discord_runner_tasks()

    late_entered = asyncio.Event()

    async def late_runner() -> None:
        late_entered.set()

    try:
        with pytest.raises(DiscordTaskServiceClosed, match="closed"):
            runners.spawn("late", late_runner())
    finally:
        await runners.close()

    assert not late_entered.is_set()
    assert not _live_discord_runner_tasks()


def _live_discord_runner_tasks() -> tuple[asyncio.Task[object], ...]:
    return tuple(
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("discord-task:") and not task.done()
    )


@pytest.mark.asyncio
async def test_stop_does_not_render_stopping_after_runner_reaches_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)
    harness.presentation.render_calls.clear()

    async def interrupt_after_completion(_channel_id: int) -> bool:
        release.set()
        await wait_for_state(harness.store, task.task_id, DiscordTaskState.STOPPED)
        return True

    monkeypatch.setattr(harness.agent, "interrupt", interrupt_after_completion)

    stopped = await harness.service.stop(task.task_id, access(), interaction_id=603)
    await harness.service.close()

    assert stopped.state is DiscordTaskState.STOPPED
    assert [record.state for record in harness.presentation.render_calls] == [
        DiscordTaskState.STOPPING,
        DiscordTaskState.STOPPED,
    ]


@pytest.mark.asyncio
async def test_retried_stop_does_not_render_stopping_after_runner_reaches_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    release = harness.agent.block_channel(10)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)

    async def failed_interrupt(_channel_id: int) -> bool:
        raise RuntimeError("interrupt transport failed")

    monkeypatch.setattr(harness.agent, "interrupt", failed_interrupt)
    with pytest.raises(RuntimeError, match="interrupt transport failed"):
        await harness.service.stop(task.task_id, access(), interaction_id=604)
    harness.presentation.render_calls.clear()

    async def interrupt_after_completion(_channel_id: int) -> bool:
        release.set()
        await wait_for_state(harness.store, task.task_id, DiscordTaskState.STOPPED)
        return True

    monkeypatch.setattr(harness.agent, "interrupt", interrupt_after_completion)

    stopped = await harness.service.stop(task.task_id, access(), interaction_id=605)
    await harness.service.close()

    assert stopped.state is DiscordTaskState.STOPPED
    assert [record.state for record in harness.presentation.render_calls] == [
        DiscordTaskState.STOPPED
    ]


@pytest.mark.asyncio
async def test_closed_service_cleans_unaccepted_start_steer_and_continue_inputs(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    await harness.service.close()
    start_inputs = TrackingAttachments()
    steer_inputs = TrackingAttachments()
    continue_inputs = TrackingAttachments()

    with pytest.raises(DiscordTaskServiceClosed):
        await harness.service.start(request(attachments=start_inputs))
    with pytest.raises(DiscordTaskServiceClosed):
        await harness.service.steer(
            "00000000000000000000000000000001",
            access(),
            DiscordTaskSteerRequest(
                "more context",
                None,
                cast(StagedDiscordAttachments, steer_inputs),
                None,
            ),
            interaction_id=606,
        )
    with pytest.raises(DiscordTaskServiceClosed):
        await harness.service.continue_task(
            "00000000000000000000000000000001",
            access(),
            request(
                trigger_event_id=607,
                attachments=continue_inputs,
                source_kind=DiscordTaskSourceKind.CONTINUATION,
            ),
            interaction_id=607,
        )

    assert start_inputs.cleanup_calls == 1
    assert steer_inputs.cleanup_calls == 1
    assert continue_inputs.cleanup_calls == 1
