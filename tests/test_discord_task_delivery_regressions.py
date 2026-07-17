import asyncio
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentReply
from study_discord_agent.discord_delivery_resources import DiscordDeliveryLease
from study_discord_agent.discord_task_delivery import DiscordTaskDeliveryError
from study_discord_agent.discord_task_model import DiscordTaskRecord, DiscordTaskState
from study_discord_agent.discord_task_service import DiscordTaskActionUnavailable
from tests.test_discord_task_service_fixtures import (
    access,
    make_harness,
    request,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_delivery_retry_waits_for_live_failure_runner_before_consuming_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("retry safely")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_outcomes.extend(
        [DiscordTaskDeliveryError("not sent", definitive_non_delivery=True), 20_101]
    )
    failed_render_entered = asyncio.Event()
    failed_render_release = asyncio.Event()
    original_render = harness.presentation.render_card

    async def block_failed_render(record: DiscordTaskRecord) -> None:
        await original_render(record)
        if record.state is DiscordTaskState.DELIVERY_FAILED:
            failed_render_entered.set()
            await failed_render_release.wait()

    monkeypatch.setattr(harness.presentation, "render_card", block_failed_render)

    task = await harness.service.start(request())
    await failed_render_entered.wait()
    retry = asyncio.create_task(
        harness.service.retry(task.task_id, access(), interaction_id=1_200)
    )
    await asyncio.sleep(0)

    assert not retry.done()
    assert harness.store.get(task.task_id).state is DiscordTaskState.DELIVERY_FAILED

    failed_render_release.set()
    retrying = await retry
    assert retrying.state is DiscordTaskState.DELIVERING
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    assert completed.result_message_id == 20_101
    assert len(harness.presentation.deliver_calls) == 2
    await harness.service.close()


@pytest.mark.asyncio
async def test_concurrent_delivery_retries_send_the_retained_lease_only_once(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("one retry")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_outcomes.extend(
        [DiscordTaskDeliveryError("not sent", definitive_non_delivery=True), 20_102]
    )
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.DELIVERY_FAILED)
    harness.presentation.delivery_entered = asyncio.Event()
    harness.presentation.delivery_release = asyncio.Event()

    first = asyncio.create_task(
        harness.service.retry(task.task_id, access(), interaction_id=1_201)
    )
    second = asyncio.create_task(
        harness.service.retry(task.task_id, access(), interaction_id=1_202)
    )
    await harness.presentation.delivery_entered.wait()
    harness.presentation.delivery_release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert sum(isinstance(result, DiscordTaskActionUnavailable) for result in results) == 1
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    assert completed.result_message_id == 20_102
    assert len(harness.presentation.deliver_calls) == 2
    retried_reply = harness.presentation.deliver_calls[1][1]
    assert retried_reply.delivery_lease is not None
    assert retried_reply.delivery_lease.closed
    await harness.service.close()


@pytest.mark.asyncio
async def test_confirmed_send_survives_lease_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("confirmed")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_release = asyncio.Event()
    task = await harness.service.start(request())
    await harness.presentation.delivery_entered.wait()
    reply = harness.presentation.deliver_calls[0][1]
    lease = reply.delivery_lease
    assert lease is not None
    original_close = DiscordDeliveryLease.close
    close_calls = 0

    def fail_first_close(current: DiscordDeliveryLease) -> None:
        nonlocal close_calls
        if current is lease:
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("cleanup unavailable")
        original_close(current)

    monkeypatch.setattr(DiscordDeliveryLease, "close", fail_first_close)
    harness.presentation.delivery_release.set()

    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    assert completed.result_message_id == 20_000
    assert not lease.closed
    await harness.service.close()
    assert lease.closed
    assert close_calls == 2


@pytest.mark.asyncio
async def test_failed_shutdown_cleanup_is_retained_for_a_later_close_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("close twice")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_release = asyncio.Event()
    await harness.service.start(request())
    await harness.presentation.delivery_entered.wait()
    reply = harness.presentation.deliver_calls[0][1]
    lease = reply.delivery_lease
    assert lease is not None
    original_close = DiscordDeliveryLease.close
    close_calls = 0

    def fail_two_closes(current: DiscordDeliveryLease) -> None:
        nonlocal close_calls
        if current is lease:
            close_calls += 1
            if close_calls <= 2:
                raise RuntimeError("cleanup unavailable")
        original_close(current)

    monkeypatch.setattr(DiscordDeliveryLease, "close", fail_two_closes)

    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        await harness.service.close()
    assert not lease.closed

    await harness.service.close()
    assert lease.closed
    assert close_calls == 3
