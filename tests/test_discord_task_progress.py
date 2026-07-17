import asyncio

import pytest

from study_discord_agent.agent_progress import AgentPlanStep, AgentProgress
from study_discord_agent.discord_task_progress import DiscordTaskProgressCoordinator


@pytest.mark.asyncio
async def test_partial_progress_updates_coalesce_without_losing_fields() -> None:
    rendered: list[AgentProgress] = []
    rendered_once = asyncio.Event()

    async def render(_task_id: str, progress: AgentProgress) -> None:
        rendered.append(progress)
        rendered_once.set()

    coordinator = DiscordTaskProgressCoordinator(render, min_edit_interval_seconds=0.02)

    await coordinator.update(
        "task-1",
        AgentProgress(
            now="Running tests",
            completed="Updated one file",
            plan=(AgentPlanStep("Verify", "inProgress"),),
        ),
    )
    await coordinator.update(
        "task-1",
        AgentProgress(now="Reviewing results", next_step="Deploy"),
    )
    await asyncio.wait_for(rendered_once.wait(), timeout=0.5)
    await asyncio.sleep(0.03)

    assert rendered == [
        AgentProgress(
            now="Reviewing results",
            completed="Updated one file",
            next_step="Deploy",
            plan=(AgentPlanStep("Verify", "inProgress"),),
        )
    ]
    await coordinator.close()


@pytest.mark.asyncio
async def test_finish_cancels_a_delayed_progress_render() -> None:
    rendered: list[AgentProgress] = []

    async def render(_task_id: str, progress: AgentProgress) -> None:
        rendered.append(progress)

    coordinator = DiscordTaskProgressCoordinator(render, min_edit_interval_seconds=0.1)
    await coordinator.update("task-1", AgentProgress(now="Still running"))
    await coordinator.finish("task-1")
    await asyncio.sleep(0.12)

    assert rendered == []
    assert await coordinator.snapshot("task-1") is None
    await coordinator.close()


@pytest.mark.asyncio
async def test_update_during_render_is_flushed_after_the_throttle_window() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    rendered: list[AgentProgress] = []

    async def render(_task_id: str, progress: AgentProgress) -> None:
        rendered.append(progress)
        if len(rendered) == 1:
            entered.set()
            await release.wait()

    coordinator = DiscordTaskProgressCoordinator(render, min_edit_interval_seconds=0.01)
    await coordinator.update("task-1", AgentProgress(now="First"))
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    await coordinator.update("task-1", AgentProgress(now="Latest"))
    release.set()

    async with asyncio.timeout(0.5):
        while len(rendered) < 2:
            await asyncio.sleep(0.005)

    assert [progress.now for progress in rendered] == ["First", "Latest"]
    await coordinator.close()
