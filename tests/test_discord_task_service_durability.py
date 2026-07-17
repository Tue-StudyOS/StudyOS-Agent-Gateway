from pathlib import Path

import pytest

from study_discord_agent.discord_task_model import (
    DiscordTaskSourceKind,
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
@pytest.mark.parametrize("unknown_stage", ["create", "card", "result"])
async def test_unknown_durability_is_reread_before_any_repeat_side_effect(
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
async def test_unknown_continuation_link_is_reread_without_duplicate_child(
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
