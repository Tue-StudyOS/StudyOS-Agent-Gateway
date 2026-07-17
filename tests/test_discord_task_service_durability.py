from pathlib import Path

import pytest

from study_discord_agent.agent import AgentChannelCapabilities
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_model import (
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError
from tests.test_discord_task_service_fixtures import (
    access,
    make_harness,
    request,
    stored_record,
    wait_for_state,
)


def _fail_directory_sync(_path: Path) -> None:
    raise OSError("sync unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_stage", ["create", "card", "result"])
async def test_unknown_durability_is_confirmed_before_any_repeat_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unknown_stage: str,
) -> None:
    harness = make_harness(tmp_path)
    target_sync = {"create": 1, "card": 2, "result": 5}[unknown_stage]
    sync_calls = 0
    raised = False

    def report_target_sync_unknown(_path: Path) -> None:
        nonlocal raised, sync_calls
        sync_calls += 1
        if sync_calls == target_sync:
            raised = True
            raise OSError("directory sync unknown")

    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        report_target_sync_unknown,
    )

    task = await harness.service.start(request())
    completed = await wait_for_state(
        harness.store, task.task_id, DiscordTaskState.COMPLETED
    )

    assert raised
    assert completed.card_message_id is not None
    assert completed.result_message_id is not None
    assert len(harness.presentation.create_calls) == 1
    assert len(harness.presentation.deliver_calls) == 1
    assert len(harness.agent.ask_calls) == 1
    await harness.service.close()


@pytest.mark.asyncio
async def test_unknown_continuation_link_is_confirmed_without_duplicate_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    parent = stored_record(
        "0000000000000000000000000000000a", DiscordTaskState.COMPLETED
    )
    harness.store.create(parent)
    raised = False

    def report_first_sync_unknown(_path: Path) -> None:
        nonlocal raised
        if not raised:
            raised = True
            raise OSError("directory sync unknown")

    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        report_first_sync_unknown,
    )
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)
    harness.agent.block_channel(10)

    child = await harness.service.continue_task(
        parent.task_id,
        access(),
        request(
            trigger_event_id=400,
            source_kind=DiscordTaskSourceKind.CONTINUATION,
        ),
        interaction_id=1_000,
    )

    assert raised
    assert harness.store.get(parent.task_id).continued_to_task_id == child.task_id
    matching = tuple(
        record for record in harness.store.records() if record.task_id == child.task_id
    )
    assert len(matching) == 1
    await wait_for_state(harness.store, child.task_id, DiscordTaskState.RUNNING)
    harness.agent.ask_release[10].set()
    await wait_for_state(harness.store, child.task_id, DiscordTaskState.COMPLETED)
    assert len(harness.presentation.create_calls) == 1
    await harness.service.close()


@pytest.mark.asyncio
async def test_create_fails_closed_when_directory_sync_cannot_be_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    sync_calls = 0

    def fail_directory_sync(_path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        raise OSError("directory sync unavailable")

    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        fail_directory_sync,
    )

    with pytest.raises(TaskStoreDurabilityError):
        await harness.service.start(request())

    assert sync_calls == 2
    assert not harness.presentation.create_calls
    assert not harness.agent.ask_calls
    await harness.service.close()


@pytest.mark.asyncio
async def test_update_fails_closed_before_interrupt_when_sync_confirmation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    record = stored_record(
        "00000000000000000000000000000001", DiscordTaskState.RUNNING
    )
    harness.store.create(record)
    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        _fail_directory_sync,
    )

    with pytest.raises(TaskStoreDurabilityError):
        await harness.service.stop(record.task_id, access(), interaction_id=1_001)

    assert not harness.agent.interrupt_calls
    assert not harness.presentation.render_calls
    await harness.service.close()


@pytest.mark.asyncio
async def test_link_fails_closed_before_continuation_runner_when_confirmation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    parent = stored_record(
        "0000000000000000000000000000000a", DiscordTaskState.COMPLETED
    )
    harness.store.create(parent)
    harness.agent.capabilities[10] = AgentChannelCapabilities(False, True, True, False)
    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        _fail_directory_sync,
    )

    with pytest.raises(TaskStoreDurabilityError):
        await harness.service.continue_task(
            parent.task_id,
            access(),
            request(
                trigger_event_id=401,
                source_kind=DiscordTaskSourceKind.CONTINUATION,
            ),
            interaction_id=1_002,
        )

    assert not harness.presentation.create_calls
    assert not harness.agent.ask_calls
    assert not harness.presentation.render_calls
    await harness.service.close()


@pytest.mark.asyncio
async def test_forget_fails_closed_before_cached_delivery_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    record = stored_record(
        "00000000000000000000000000000001", DiscordTaskState.COMPLETED
    )
    harness.store.create(record)
    harness.cache.put(record.task_id, PreparedDiscordReply("cached", files=()))
    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        _fail_directory_sync,
    )

    with pytest.raises(TaskStoreDurabilityError):
        await harness.service.forget(record.task_id, access(), interaction_id=1_003)

    cached = harness.cache.consume(record.task_id, (tmp_path,), 1_000)
    assert cached is not None
    assert cached.delivery_lease is not None
    cached.delivery_lease.close()
    await harness.service.close()


@pytest.mark.asyncio
async def test_reconcile_fails_closed_before_capability_or_card_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    record = stored_record(
        "00000000000000000000000000000001", DiscordTaskState.RUNNING
    )
    harness.store.create(record)
    monkeypatch.setattr(
        "study_discord_agent.discord_task_persistence._fsync_directory",
        _fail_directory_sync,
    )

    with pytest.raises(TaskStoreDurabilityError):
        await harness.service.reconcile_startup()

    assert not harness.presentation.render_calls
    await harness.service.close()
