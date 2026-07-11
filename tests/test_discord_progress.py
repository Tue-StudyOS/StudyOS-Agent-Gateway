import asyncio
from typing import Any, cast

import pytest

from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.discord_progress import DiscordProgressMessage


class FakeStatusMessage:
    def __init__(self, content: str | None = None) -> None:
        self.content = content
        self.edits: list[str | None] = []
        self.deleted = False

    async def edit(self, *, content: str | None = None) -> None:
        self.content = content
        self.edits.append(content)

    async def delete(self) -> None:
        self.deleted = True


class FakeSourceMessage:
    def __init__(self) -> None:
        self.status: FakeStatusMessage | None = None

    async def reply(self, content: str) -> FakeStatusMessage:
        self.status = FakeStatusMessage(content)
        return self.status


@pytest.mark.asyncio
async def test_progress_updates_coalesce_into_latest_edit() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source), min_edit_interval_seconds=0.05
    )

    await progress.update(AgentProgress(now="Running tests", completed="Updated one file"))
    await progress.update(AgentProgress(now="Reviewing results", next_step="Finish verification"))
    await asyncio.sleep(0.08)

    assert source.status is not None
    assert len(source.status.edits) == 1
    rendered = source.status.edits[0]
    assert rendered is not None
    assert "Reviewing results" in rendered
    assert "Updated one file" in rendered
    assert "Finish verification" in rendered


@pytest.mark.asyncio
async def test_delete_stops_future_edits() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(cast(Any, source), 0)

    await progress.delete()
    await progress.update(AgentProgress(now="Must not render"))
    await asyncio.sleep(0)

    assert source.status is not None
    assert source.status.deleted
    assert source.status.edits == []


@pytest.mark.asyncio
async def test_failure_reuses_status_message() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(cast(Any, source), 0)

    await progress.fail()

    assert source.status is not None
    assert source.status.edits == [
        "❌ **Agent failed**\nThe task could not be completed. Details were logged."
    ]
    assert not source.status.deleted
