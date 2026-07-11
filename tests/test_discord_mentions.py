import asyncio
from pathlib import Path
from typing import Any, cast

import discord
import pytest
from pydantic import SecretStr

from study_discord_agent.agent import AgentGateway, AgentReply, ProgressSink
from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.codex_app_server_runtime import AgentTurnInterrupted, SteerResult
from study_discord_agent.config import Settings
from study_discord_agent.discord_mentions import DiscordMentionCoordinator
from study_discord_agent.discord_origin import DiscordOriginContext


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.steers: list[dict[str, object]] = []
        self.interrupts: list[int] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.interrupted = False
        self.interrupt_result = True
        self.steer_result = SteerResult.STEERED
        self.reply_message = "done"

    async def ask(self, **kwargs: object) -> AgentReply:
        self.calls.append(kwargs)
        self.started.set()
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            await cast(ProgressSink, on_progress)(AgentProgress(now="Inspecting the gateway"))
        if self.block:
            await self.release.wait()
        if self.interrupted:
            raise AgentTurnInterrupted("stopped")
        return AgentReply(message=self.reply_message)

    async def steer(self, **kwargs: object) -> SteerResult:
        self.steers.append(kwargs)
        return self.steer_result

    async def interrupt(self, channel_id: int) -> bool:
        self.interrupts.append(channel_id)
        if not self.interrupt_result:
            return False
        self.interrupted = True
        self.release.set()
        return True


class FakeSentMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.edits: list[str | None] = []
        self.deleted = False

    async def edit(self, *, content: str | None = None, **_: object) -> None:
        self.content = content
        self.edits.append(content)

    async def delete(self) -> None:
        self.deleted = True


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        channel_id: int = 123,
        *,
        fail_file_upload: bool = False,
    ) -> None:
        self.id = message_id
        self.channel = type("Channel", (), {"id": channel_id})()
        self.author = FakeAuthor()
        self.attachments: list[object] = []
        self.sent: list[FakeSentMessage] = []
        self.reply_calls: list[dict[str, object]] = []
        self.fail_file_upload = fail_file_upload

    async def reply(self, content: str | None = None, **kwargs: object) -> FakeSentMessage:
        self.reply_calls.append(kwargs)
        if self.fail_file_upload and kwargs.get("files"):
            raise RuntimeError("upload failed")
        sent = FakeSentMessage(content)
        self.sent.append(sent)
        return sent


def _coordinator(
    agent: FakeAgent,
    artifact_root: str = "/tmp/studyos-artifacts",
) -> DiscordMentionCoordinator:
    settings = Settings(
        discord_token=SecretStr("test-token"),
        discord_attachment_dir="/tmp/studyos-discord-attachments",
        discord_artifact_allowed_roots=artifact_root,
    )
    return DiscordMentionCoordinator(settings, cast(AgentGateway, agent))


def _origin(channel_id: int = 123) -> DiscordOriginContext:
    return DiscordOriginContext(channel_id=channel_id, channel_type="text")


class FakeAuthor:
    def __str__(self) -> str:
        return "student"


async def _wait_until(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.mark.asyncio
async def test_initial_status_is_deleted_after_final_reply() -> None:
    agent = FakeAgent()
    coordinator = _coordinator(agent)
    message = FakeMessage(1)

    await coordinator.dispatch(cast(Any, message), "hello", _origin())
    await _wait_until(lambda: len(message.sent) == 2)

    assert message.sent[0].content is not None
    assert "Working" in message.sent[0].content
    assert message.sent[0].deleted
    assert message.sent[1].content == "done"


@pytest.mark.asyncio
async def test_code_reply_becomes_temporary_markdown_attachment(tmp_path: Path) -> None:
    agent = FakeAgent()
    agent.reply_message = "Here's the snippet:\n\n```python\nprint('hi')\n```"
    coordinator = _coordinator(agent, str(tmp_path))
    message = FakeMessage(1)

    await coordinator.dispatch(cast(Any, message), "show code", _origin())
    await _wait_until(lambda: len(message.sent) == 2)

    assert "Full write-up's attached" in str(message.sent[1].content)
    files = cast(list[discord.File], message.reply_calls[1]["files"])
    assert files[0].filename == "reply-1.md"
    assert not (tmp_path / "discord-replies/reply-1.md").exists()


@pytest.mark.asyncio
async def test_generated_attachment_is_cleaned_when_upload_fails(tmp_path: Path) -> None:
    agent = FakeAgent()
    agent.reply_message = "Here's the snippet:\n\n```python\nprint('hi')\n```"
    coordinator = _coordinator(agent, str(tmp_path))
    message = FakeMessage(1, fail_file_upload=True)

    await coordinator.dispatch(cast(Any, message), "show code", _origin())
    await _wait_until(lambda: bool(message.sent and message.sent[0].edits))

    assert "Agent failed" in str(message.sent[0].content)
    assert not (tmp_path / "discord-replies/reply-1.md").exists()


@pytest.mark.asyncio
async def test_same_channel_followup_steers_without_second_handler() -> None:
    agent = FakeAgent()
    agent.block = True
    coordinator = _coordinator(agent)
    first = FakeMessage(1)
    followup = FakeMessage(2)

    await coordinator.dispatch(cast(Any, first), "slow first", _origin())
    await agent.started.wait()
    await coordinator.dispatch(cast(Any, followup), "use the new direction", _origin())

    assert len(agent.calls) == 1
    assert len(agent.steers) == 1
    assert agent.steers[0]["source_message_id"] == 2
    assert followup.sent == []
    agent.release.set()
    await _wait_until(lambda: len(first.sent) == 2)
    assert first.sent[1].content == "done"


@pytest.mark.asyncio
async def test_stop_interrupts_protocol_turn_instead_of_cancelling_task() -> None:
    agent = FakeAgent()
    agent.block = True
    coordinator = _coordinator(agent)
    first = FakeMessage(1)
    stop = FakeMessage(2)

    await coordinator.dispatch(cast(Any, first), "slow first", _origin())
    await agent.started.wait()
    await coordinator.dispatch(cast(Any, stop), "stop working", _origin())
    await _wait_until(lambda: first.sent[0].deleted)

    assert len(agent.calls) == 1
    assert agent.interrupts == [123]
    assert stop.sent[0].content == "Stopped the active task in this channel."
    assert len(first.sent) == 1


@pytest.mark.asyncio
async def test_stop_cancels_local_startup_when_no_protocol_turn_exists() -> None:
    agent = FakeAgent()
    agent.block = True
    agent.interrupt_result = False
    coordinator = _coordinator(agent)
    first = FakeMessage(1)
    stop = FakeMessage(2)

    await coordinator.dispatch(cast(Any, first), "slow first", _origin())
    await agent.started.wait()
    await coordinator.dispatch(cast(Any, stop), "stop working", _origin())

    assert stop.sent[0].content == "Stopped the active task in this channel."
    assert first.sent[0].deleted


@pytest.mark.asyncio
async def test_concurrent_unsteerable_followups_are_not_dropped() -> None:
    agent = FakeAgent()
    agent.block = True
    agent.steer_result = SteerResult.NOT_STEERABLE
    coordinator = _coordinator(agent)
    first = FakeMessage(1)
    second = FakeMessage(2)
    third = FakeMessage(3)

    await coordinator.dispatch(cast(Any, first), "slow first", _origin())
    await agent.started.wait()
    followups = asyncio.gather(
        coordinator.dispatch(cast(Any, second), "second", _origin()),
        coordinator.dispatch(cast(Any, third), "third", _origin()),
    )
    await _wait_until(lambda: len(agent.steers) == 2)
    agent.release.set()
    await followups
    await _wait_until(lambda: len(agent.calls) == 3)

    prompts = {str(call["prompt"]) for call in agent.calls}
    assert prompts == {"slow first", "second", "third"}


@pytest.mark.asyncio
async def test_duplicate_message_id_runs_once() -> None:
    agent = FakeAgent()
    coordinator = _coordinator(agent)
    message = FakeMessage(1)

    await coordinator.dispatch(cast(Any, message), "hello", _origin())
    await coordinator.dispatch(cast(Any, message), "hello", _origin())
    await _wait_until(lambda: len(message.sent) == 2)

    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_different_channels_run_in_parallel() -> None:
    agent = FakeAgent()
    agent.block = True
    coordinator = _coordinator(agent)
    first = FakeMessage(1, 101)
    second = FakeMessage(2, 202)

    await asyncio.gather(
        coordinator.dispatch(cast(Any, first), "first", _origin(101)),
        coordinator.dispatch(cast(Any, second), "second", _origin(202)),
    )
    await _wait_until(lambda: len(agent.calls) == 2)

    assert {call["channel_id"] for call in agent.calls} == {101, 202}
    agent.release.set()
    await _wait_until(lambda: len(first.sent) == 2 and len(second.sent) == 2)
