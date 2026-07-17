import asyncio
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentReply
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_delivery import DiscordTaskDeliveryError
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRetryMode,
    DiscordTaskState,
)
from tests.test_discord_task_service_fixtures import (
    access,
    make_harness,
    request,
    stored_record,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_delivery_enters_delivering_and_uses_a_pinned_lease(tmp_path: Path) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("stable result")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_release = asyncio.Event()

    task = await harness.service.start(request())
    await harness.presentation.delivery_entered.wait()

    delivering = harness.store.get(task.task_id)
    sent_record, sent_reply = harness.presentation.deliver_calls[0]
    assert delivering.state is DiscordTaskState.DELIVERING
    assert sent_record.state is DiscordTaskState.DELIVERING
    assert sent_reply.delivery_lease is not None
    assert sent_reply.delivery_lease.files[0].stream.read() == b"stable result"

    harness.presentation.delivery_release.set()
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    assert completed.result_message_id == 20_000
    assert sent_reply.delivery_lease.closed
    await harness.service.close()


@pytest.mark.asyncio
async def test_definitive_delivery_failure_restores_exact_lease_and_retry_skips_agent(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("retry me")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_outcomes.extend(
        [DiscordTaskDeliveryError("not sent", definitive_non_delivery=True), 20_001]
    )

    task = await harness.service.start(request())
    failed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.DELIVERY_FAILED
    )
    first_reply = harness.presentation.deliver_calls[0][1]

    assert failed.failure is not None
    assert failed.failure.retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY
    assert first_reply.delivery_lease is not None
    assert not first_reply.delivery_lease.closed
    agent_calls = len(harness.agent.ask_calls)

    retrying = await harness.service.retry(
        task.task_id, access(), interaction_id=700
    )
    assert retrying.state is DiscordTaskState.DELIVERING
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )
    second_reply = harness.presentation.deliver_calls[1][1]
    assert second_reply is first_reply
    assert completed.result_message_id == 20_001
    assert len(harness.agent.ask_calls) == agent_calls
    second_lease = second_reply.delivery_lease
    assert second_lease is not None and second_lease.closed
    await harness.service.close()


@pytest.mark.asyncio
async def test_ambiguous_delivery_closes_lease_and_disables_retry(tmp_path: Path) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("maybe sent")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_outcomes.append(RuntimeError("transport vanished"))

    task = await harness.service.start(request())
    failed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.DELIVERY_FAILED
    )
    reply = harness.presentation.deliver_calls[0][1]

    assert failed.failure is not None
    assert failed.failure.retry_mode is DiscordTaskRetryMode.NONE
    assert reply.delivery_lease is not None and reply.delivery_lease.closed
    with pytest.raises(RuntimeError, match="retry"):
        await harness.service.retry(task.task_id, access(), interaction_id=701)
    await harness.service.close()


@pytest.mark.asyncio
async def test_delivery_retry_without_cache_stays_failed_and_disables_retry(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    failure = DiscordTaskFailure(
        category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
        summary="Discord could not deliver the result. It is safe to retry delivery.",
        retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
    )
    record = stored_record(
        "00000000000000000000000000000001",
        DiscordTaskState.DELIVERY_FAILED,
        failure=failure,
    )
    harness.store.create(record)

    result = await harness.service.retry(record.task_id, access(), interaction_id=702)

    assert result.state is DiscordTaskState.DELIVERY_FAILED
    assert result.failure is not None
    assert result.failure.retry_mode is DiscordTaskRetryMode.NONE
    assert not harness.agent.ask_calls
    assert not harness.presentation.deliver_calls
    await harness.service.close()


@pytest.mark.asyncio
async def test_close_cancels_delivery_and_closes_in_flight_lease(tmp_path: Path) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("cancel")
    harness = make_harness(tmp_path)
    harness.agent.replies[10] = AgentReply("done", files=(artifact,))
    harness.presentation.delivery_release = asyncio.Event()

    await harness.service.start(request())
    await harness.presentation.delivery_entered.wait()
    reply: PreparedDiscordReply = harness.presentation.deliver_calls[0][1]

    await harness.service.close()

    assert reply.delivery_lease is not None and reply.delivery_lease.closed
