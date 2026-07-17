import asyncio
from pathlib import Path

import pytest

from study_discord_agent.discord_task_model import DiscordTaskRecord, DiscordTaskState
from tests.test_discord_task_service_fixtures import (
    access,
    make_harness,
    request,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_stop_interrupts_before_waiting_for_discord_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(tmp_path)
    agent_release = harness.agent.block_channel(10)
    task = await harness.service.start(request())
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.RUNNING)
    render_entered = asyncio.Event()
    render_release = asyncio.Event()
    original_render = harness.presentation.render_card

    async def slow_stopping_render(record: DiscordTaskRecord) -> None:
        if record.state is DiscordTaskState.STOPPING:
            render_entered.set()
            await render_release.wait()
        await original_render(record)

    monkeypatch.setattr(harness.presentation, "render_card", slow_stopping_render)

    stopping = asyncio.create_task(
        harness.service.stop(task.task_id, access(), interaction_id=608)
    )
    await render_entered.wait()

    assert harness.agent.interrupt_calls == [10]
    render_release.set()
    accepted = await stopping
    assert accepted.state is DiscordTaskState.STOPPING

    agent_release.set()
    await wait_for_state(harness.store, task.task_id, DiscordTaskState.STOPPED)
    await harness.service.close()
