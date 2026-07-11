import asyncio
from typing import Any, cast

import discord
import pytest

from study_discord_agent.agent_progress import AgentPlanStep, AgentProgress
from study_discord_agent.discord_progress import DiscordProgressMessage


class FakeStatusMessage:
    def __init__(self, content: str | None = None, view: object | None = None) -> None:
        self.content = content
        self.view = view
        self.edits: list[object | None] = []
        self.rendered_edits: list[str] = []
        self.deleted = False

    async def edit(
        self,
        *,
        content: str | None = None,
        view: object | None = None,
        **_: object,
    ) -> None:
        self.content = content
        self.view = view
        self.edits.append(view)
        self.rendered_edits.append(_rendered(view))

    async def delete(self) -> None:
        self.deleted = True


class FakeSourceMessage:
    def __init__(self) -> None:
        self.status: FakeStatusMessage | None = None
        self.author = type("Author", (), {"id": 42})()

    async def reply(self, content: str | None = None, **kwargs: object) -> FakeStatusMessage:
        self.status = FakeStatusMessage(content, kwargs.get("view"))
        return self.status


async def _stop() -> bool:
    return True


def _rendered(view: object | None) -> str:
    assert isinstance(view, discord.ui.LayoutView)
    return "\n".join(
        child.content
        for child in view.walk_children()
        if isinstance(child, discord.ui.TextDisplay)
    )


def _stop_button(
    view: object | None,
) -> discord.ui.Button[discord.ui.LayoutView]:
    assert isinstance(view, discord.ui.LayoutView)
    button = next(
        child for child in view.walk_children() if isinstance(child, discord.ui.Button)
    )
    return cast(discord.ui.Button[discord.ui.LayoutView], button)


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.edits: list[object | None] = []
        self.messages: list[tuple[str, bool]] = []

    async def edit_message(self, *, view: object | None = None, **_: object) -> None:
        self.edits.append(view)

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append((content, ephemeral))


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append((content, ephemeral))


class FakeInteraction:
    def __init__(self, user_id: int) -> None:
        self.user = type("User", (), {"id": user_id})()
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()
        self.original_edits: list[object | None] = []

    async def edit_original_response(
        self, *, view: object | None = None, **_: object
    ) -> None:
        self.original_edits.append(view)


@pytest.mark.asyncio
async def test_progress_updates_coalesce_into_latest_edit() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source),
        _stop,
        min_edit_interval_seconds=0.05,
        animation_interval_seconds=0,
    )

    await progress.update(AgentProgress(now="Running tests", completed="Updated one file"))
    await progress.update(AgentProgress(now="Reviewing results", next_step="Finish verification"))
    await asyncio.sleep(0.08)

    assert source.status is not None
    assert len(source.status.edits) == 1
    rendered = _rendered(source.status.edits[0])
    assert "Reviewing results" in rendered
    assert "Updated one file" in rendered
    assert "Finish verification" in rendered


@pytest.mark.asyncio
async def test_delete_stops_future_edits() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source), _stop, 0, animation_interval_seconds=0
    )

    await progress.delete()
    await progress.update(AgentProgress(now="Must not render"))
    await asyncio.sleep(0)

    assert source.status is not None
    assert source.status.deleted
    assert source.status.edits == []


@pytest.mark.asyncio
async def test_failure_reuses_status_message() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source), _stop, 0, animation_interval_seconds=0
    )

    await progress.fail()

    assert source.status is not None
    assert len(source.status.edits) == 1
    assert "Agent failed" in _rendered(source.status.edits[0])
    assert not source.status.deleted


@pytest.mark.asyncio
async def test_structured_plan_renders_as_bounded_checklist() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source), _stop, 0, animation_interval_seconds=0
    )

    await progress.update(
        AgentProgress(
            now="Running focused tests",
            plan=(
                AgentPlanStep("Inspect the gateway", "completed"),
                AgentPlanStep("Build the progress card", "inProgress"),
                AgentPlanStep("Deploy to the Jetson", "pending"),
            ),
        )
    )
    await asyncio.sleep(0)

    assert source.status is not None
    rendered = _rendered(source.status.view)
    assert "`[x]` Inspect the gateway" in rendered
    assert "`[-]` Build the progress card" in rendered
    assert "`[ ]` Deploy to the Jetson" in rendered
    assert "Now: Running focused tests" in rendered


@pytest.mark.asyncio
async def test_active_plan_step_cycles_through_ascii_spinner() -> None:
    source = FakeSourceMessage()
    progress = await DiscordProgressMessage.create(
        cast(Any, source),
        _stop,
        min_edit_interval_seconds=0,
        animation_interval_seconds=0.01,
    )

    await progress.update(
        AgentProgress(
            plan=(AgentPlanStep("Build the progress card", "inProgress"),)
        )
    )
    await asyncio.sleep(0.045)
    await progress.delete()

    assert source.status is not None
    spinner_markers = [
        next(
            marker
            for marker in ("`[-]`", "`[\\]`", "`[/]`", "`[|]`")
            if marker in rendered
        )
        for rendered in source.status.rendered_edits
    ]
    assert spinner_markers[:5] == ["`[-]`", "`[\\]`", "`[/]`", "`[|]`", "`[-]`"]


@pytest.mark.asyncio
async def test_stop_button_acknowledges_and_invokes_callback_once() -> None:
    calls = 0

    async def stop() -> bool:
        nonlocal calls
        calls += 1
        return True

    source = FakeSourceMessage()
    await DiscordProgressMessage.create(
        cast(Any, source), stop, 0, animation_interval_seconds=0
    )
    assert source.status is not None
    button = _stop_button(source.status.view)
    first = FakeInteraction(42)
    second = FakeInteraction(42)

    await button.callback(cast(Any, first))
    await button.callback(cast(Any, second))

    assert calls == 1
    assert button.disabled
    assert first.response.edits == [source.status.view]
    assert first.followup.messages == [("Stopping it now.", True)]
    assert second.response.messages == [("Already stopping it.", True)]


@pytest.mark.asyncio
async def test_stop_button_rejects_other_users() -> None:
    calls = 0

    async def stop() -> bool:
        nonlocal calls
        calls += 1
        return True

    source = FakeSourceMessage()
    await DiscordProgressMessage.create(
        cast(Any, source), stop, 0, animation_interval_seconds=0
    )
    assert source.status is not None
    interaction = FakeInteraction(99)

    await _stop_button(source.status.view).callback(cast(Any, interaction))

    assert calls == 0
    assert interaction.response.messages == [
        ("Only the person who started this task can stop it.", True)
    ]
