from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.agent import AgentChannelCapabilities, AgentExecutionContext
from study_discord_agent.agent_execution_policy import AgentPolicyClass, execution_policy
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_model import (
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
    DiscordTaskState,
    transition,
)
from study_discord_agent.discord_task_service import DiscordTaskActionUnavailable
from tests.test_discord_task_service_fixtures import (
    NOW,
    TrackingAttachments,
    access,
    make_harness,
    request,
    stored_record,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_continue_links_only_latest_created_completed_task_and_rerenders_parent(
    tmp_path: Path,
) -> None:
    resolved: list[DiscordTaskRecord] = []
    policy = execution_policy(AgentPolicyClass.SECURITY_REVIEW)

    def resolve(record: DiscordTaskRecord) -> AgentExecutionContext:
        resolved.append(record)
        return AgentExecutionContext(
            channel_id=record.execution_channel_id,
            trigger_event_id=record.trigger_event_id,
            repository_full_name="Tue-StudyOS/example",
            repository_commit_sha=record.repository_commit_sha,
            execution_policy=policy,
        )

    harness = make_harness(tmp_path, execution_context_resolver=resolve)
    older = stored_record(
        "0000000000000000000000000000000a",
        DiscordTaskState.COMPLETED,
        created_at=NOW - timedelta(minutes=1),
    )
    latest = stored_record(
        "0000000000000000000000000000000b",
        DiscordTaskState.COMPLETED,
        intent=DiscordTaskIntent.SECURITY_REVIEW,
        source_reference_id="a" * 32,
        repository_commit_sha="b" * 40,
    )
    harness.store.create(older)
    harness.store.create(latest)
    continuation = request(
        trigger_event_id=300,
        prompt="continue with this",
        source_kind=DiscordTaskSourceKind.CONTINUATION,
        intent=DiscordTaskIntent.IMPLEMENTATION,
        source_reference_id="c" * 32,
        repository_commit_sha="d" * 40,
        task_id="123e4567-e89b-12d3-a456-426614174000",
    )

    with pytest.raises(DiscordTaskActionUnavailable, match="latest"):
        await harness.service.continue_task(
            older.task_id, access(), continuation, interaction_id=900
        )
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)
    harness.agent.block_channel(10)
    child = await harness.service.continue_task(
        latest.task_id, access(), continuation, interaction_id=901
    )

    parent = harness.store.get(latest.task_id)
    assert parent.continued_to_task_id == child.task_id
    assert child.continued_from_task_id == parent.task_id
    assert child.task_id == "123e4567-e89b-12d3-a456-426614174000"
    assert child.intent is parent.intent is DiscordTaskIntent.SECURITY_REVIEW
    assert child.source_reference_id == parent.source_reference_id == "a" * 32
    assert child.repository_commit_sha == parent.repository_commit_sha == "b" * 40
    assert parent in harness.presentation.render_calls
    await wait_for_state(harness.store, child.task_id, DiscordTaskState.RUNNING)
    harness.agent.ask_release[10].set()
    await wait_for_state(harness.store, child.task_id, DiscordTaskState.COMPLETED)
    execution = cast(AgentExecutionContext, harness.agent.ask_calls[-1]["execution"])
    assert execution.require_existing_session
    assert resolved[-1].task_id == child.task_id
    await harness.service.close()


@pytest.mark.asyncio
async def test_forget_atomically_unlinks_both_neighbors_and_discards_cache(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    parent = stored_record("00000000000000000000000000000001", DiscordTaskState.COMPLETED)
    child = replace(
        stored_record("00000000000000000000000000000002", DiscordTaskState.STARTING),
        continued_from_task_id=parent.task_id,
    )
    grandchild = replace(
        stored_record("00000000000000000000000000000003", DiscordTaskState.STARTING),
        continued_from_task_id=child.task_id,
    )
    harness.store.create(parent)
    harness.store.link_child(parent.task_id, 0, child)
    child = harness.store.compare_and_set(
        child.task_id,
        0,
        lambda record: transition(record, DiscordTaskState.RUNNING, NOW.isoformat()),
    )
    child = harness.store.compare_and_set(
        child.task_id,
        child.revision,
        lambda record: transition(record, DiscordTaskState.DELIVERING, NOW.isoformat()),
    )
    child = harness.store.compare_and_set(
        child.task_id,
        child.revision,
        lambda record: transition(record, DiscordTaskState.COMPLETED, NOW.isoformat()),
    )
    harness.store.link_child(child.task_id, child.revision, grandchild)
    harness.cache.put(child.task_id, PreparedDiscordReply("cached", files=()))

    await harness.service.forget(child.task_id, access(), interaction_id=902)

    with pytest.raises(KeyError):
        harness.store.get(child.task_id)
    assert harness.store.get(parent.task_id).continued_to_task_id is None
    assert harness.store.get(grandchild.task_id).continued_from_task_id is None
    assert harness.cache.consume(child.task_id, (tmp_path,), 1000) is None
    await harness.service.close()


@pytest.mark.asyncio
async def test_forget_rejects_active_task_and_cleans_rejected_continuation_inputs(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    task = stored_record("00000000000000000000000000000001", DiscordTaskState.RUNNING)
    harness.store.create(task)
    with pytest.raises(DiscordTaskActionUnavailable, match="active"):
        await harness.service.forget(task.task_id, access(), interaction_id=903)

    inputs = TrackingAttachments()
    continuation = request(
        trigger_event_id=301,
        attachments=inputs,
        source_kind=DiscordTaskSourceKind.CONTINUATION,
    )
    with pytest.raises(DiscordTaskActionUnavailable):
        await harness.service.continue_task(
            task.task_id, access(), continuation, interaction_id=904
        )
    assert inputs.cleanup_calls == 1
    await harness.service.close()


def test_status_active_and_list_are_authorized_bounded_and_newest_first(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    for index in range(12):
        record = stored_record(
            f"{index + 1:032x}",
            DiscordTaskState.COMPLETED,
            channel_id=index + 10,
            created_at=NOW + timedelta(minutes=index),
        )
        harness.store.create(record)
    active = stored_record(
        "00000000000000000000000000000020",
        DiscordTaskState.RUNNING,
        channel_id=50,
    )
    harness.store.create(active)
    broad_access = access(
        channel_id=50,
        visible=frozenset({*range(10, 22), 50}),
    )

    assert harness.service.status(active.task_id, broad_access) == active
    assert harness.service.active_task(50) == active
    terminal = harness.service.list_tasks(
        broad_access, scope="mine", state="terminal", current_channel_id=50
    )

    assert len(terminal) == 10
    assert [record.execution_channel_id for record in terminal] == list(range(21, 11, -1))
    channel = harness.service.list_tasks(
        broad_access, scope="channel", state="active", current_channel_id=50
    )
    assert channel == (active,)


@pytest.mark.asyncio
async def test_bounded_trigger_claim_prevents_reexecution_after_forget(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.COMPLETED)
    await harness.service.forget(task.task_id, access(), interaction_id=905)
    duplicate_inputs = TrackingAttachments()

    with pytest.raises(DiscordTaskActionUnavailable, match="already handled"):
        await harness.service.start(request(attachments=duplicate_inputs))

    assert duplicate_inputs.cleanup_calls == 1
    assert len(harness.agent.ask_calls) == 1
    await harness.service.close()


def test_list_orders_timezone_offsets_by_actual_creation_time(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    earlier = replace(
        stored_record("00000000000000000000000000000001", DiscordTaskState.COMPLETED),
        created_at="2026-07-17T13:30:00+02:00",
    )
    later = replace(
        stored_record("00000000000000000000000000000002", DiscordTaskState.COMPLETED),
        created_at="2026-07-17T12:00:00+00:00",
    )
    harness.store.create(earlier)
    harness.store.create(later)

    listed = harness.service.list_tasks(
        access(), scope="mine", state="terminal", current_channel_id=10
    )

    assert listed == (later, earlier)
