import asyncio
from collections import deque
from typing import Any

import pytest

from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.github_events import DiscordNotification


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.cancelled = 0

    async def ask(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        **_: object,
    ) -> object:
        self.calls.append({"prompt": prompt, "user": user, "channel_id": channel_id})
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return type("Reply", (), {"message": "done", "files": ()})()


class FakeBot:
    def __init__(self) -> None:
        self.settings = type(
            "Settings",
            (),
            {
                "discord_pr_channel_id": None,
                "agent_auto_review_enabled": True,
            },
        )()
        self.agent = FakeAgent()

    def get_channel(self, channel_id: int) -> None:
        raise AssertionError(f"unexpected channel lookup: {channel_id}")


@pytest.mark.asyncio
async def test_github_webhook_can_run_agent_without_discord_channel() -> None:
    bot = FakeBot()
    notification = DiscordNotification(
        title="Issue #1 opened",
        url="https://github.com/Tue-StudyOS/example/issues/1",
        description="Tue-StudyOS/example by @student",
        color=0x2DA44E,
        agent_prompt="Refine issue #1",
    )

    await StudyBot.publish_notification(bot, notification)  # type: ignore[arg-type]

    assert bot.agent.calls == [
        {
            "prompt": "Refine issue #1",
            "user": "github-webhook",
            "channel_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_discord_duplicate_message_id_runs_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = FakeDiscordMentionBot()
    channel = FakeChannel(123, "bot-dev")
    message = FakeMessage(1, "@StudyOS Bot hello", channel, bot.user)
    monkeypatch.setattr(
        "study_discord_agent.discord_bot.save_message_attachments",
        _empty_attachments,
    )

    await StudyBot.on_message(bot, message)  # type: ignore[arg-type]
    await StudyBot.on_message(bot, message)  # type: ignore[arg-type]
    await _wait_for_calls(bot.agent, 1)

    assert len(bot.agent.calls) == 1
    assert message.replies == ["done"]


@pytest.mark.asyncio
async def test_discord_same_channel_followup_cancels_previous_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = FakeDiscordMentionBot()
    bot.agent.block = True
    channel = FakeChannel(123, "bot-dev")
    first = FakeMessage(1, "@StudyOS Bot slow first", channel, bot.user)
    second = FakeMessage(2, "@StudyOS Bot use the new direction", channel, bot.user)
    monkeypatch.setattr(
        "study_discord_agent.discord_bot.save_message_attachments",
        _empty_attachments,
    )

    await StudyBot.on_message(bot, first)  # type: ignore[arg-type]
    await bot.agent.started.wait()
    bot.agent.started = asyncio.Event()
    await StudyBot.on_message(bot, second)  # type: ignore[arg-type]
    await _wait_for_calls(bot.agent, 2)
    bot.agent.release.set()
    await _wait_for_replies(second, 1)

    assert bot.agent.cancelled == 1
    assert first.replies == []
    assert second.replies == ["done"]
    assert channel.typing_entries == channel.typing_exits == 2


@pytest.mark.asyncio
async def test_discord_stop_cancels_active_task_without_new_agent_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = FakeDiscordMentionBot()
    bot.agent.block = True
    channel = FakeChannel(123, "bot-dev")
    first = FakeMessage(1, "@StudyOS Bot slow first", channel, bot.user)
    stop = FakeMessage(2, "@StudyOS Bot stop working!!!", channel, bot.user)
    monkeypatch.setattr(
        "study_discord_agent.discord_bot.save_message_attachments",
        _empty_attachments,
    )

    await StudyBot.on_message(bot, first)  # type: ignore[arg-type]
    await bot.agent.started.wait()
    await StudyBot.on_message(bot, stop)  # type: ignore[arg-type]

    assert len(bot.agent.calls) == 1
    assert bot.agent.cancelled == 1
    assert stop.replies == ["Stopped the active task in this channel."]
    assert channel.typing_entries == channel.typing_exits == 1


@pytest.mark.asyncio
async def test_discord_different_channels_run_in_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = FakeDiscordMentionBot()
    bot.agent.block = True
    first = FakeMessage(1, "@StudyOS Bot first", FakeChannel(101, "a"), bot.user)
    second = FakeMessage(2, "@StudyOS Bot second", FakeChannel(202, "b"), bot.user)
    monkeypatch.setattr(
        "study_discord_agent.discord_bot.save_message_attachments",
        _empty_attachments,
    )

    await asyncio.gather(
        StudyBot.on_message(bot, first),  # type: ignore[arg-type]
        StudyBot.on_message(bot, second),  # type: ignore[arg-type]
    )
    await _wait_for_calls(bot.agent, 2)
    bot.agent.release.set()
    await asyncio.gather(_wait_for_replies(first, 1), _wait_for_replies(second, 1))

    assert {call["channel_id"] for call in bot.agent.calls} == {101, 202}
    assert bot.agent.cancelled == 0


class FakeDiscordMentionBot:
    def __init__(self) -> None:
        self.settings = type(
            "Settings",
            (),
            {
                "discord_message_agent_enabled": True,
                "discord_attachment_dir": "/tmp/studyos-discord-attachments",
                "discord_artifact_allowed_root_list": ("/tmp/studyos-artifacts",),
                "discord_artifact_max_bytes": 8_000_000,
            },
        )()
        self.agent = FakeAgent()
        self.user = FakeDiscordUser()
        self._active_mention_tasks: dict[int, asyncio.Task[None]] = {}
        self._mention_generations: dict[int, int] = {}
        self._mention_lock = asyncio.Lock()
        self._seen_message_ids: set[int] = set()
        self._seen_message_order: deque[int] = deque()

    async def _dispatch_agent_mention(
        self,
        message: object,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> None:
        await StudyBot._dispatch_agent_mention(self, message, prompt, origin_context)  # type: ignore[arg-type]

    def _remember_seen_message_id(self, message_id: int) -> None:
        StudyBot._remember_seen_message_id(self, message_id)  # type: ignore[arg-type]

    async def _forget_mention_task(self, channel_id: int, task: asyncio.Task[None]) -> None:
        await StudyBot._forget_mention_task(self, channel_id, task)  # type: ignore[arg-type]

    async def _handle_agent_mention(
        self,
        message: object,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> None:
        await StudyBot._handle_agent_mention(self, message, prompt, origin_context)  # type: ignore[arg-type]

    async def _reply_to_message(self, message: object, reply: object) -> None:
        await StudyBot._reply_to_message(self, message, reply)  # type: ignore[arg-type]


class FakeDiscordUser:
    display_name = "StudyOS Bot"


class FakeAuthor:
    bot = False

    def __str__(self) -> str:
        return "student"


class FakeTyping:
    def __init__(self, channel: "FakeChannel") -> None:
        self._channel = channel

    async def __aenter__(self) -> None:
        self._channel.typing_entries += 1

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self._channel.typing_exits += 1


class FakeChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name
        self.category_id: int | None = None
        self.category = None
        self.typing_entries = 0
        self.typing_exits = 0

    def typing(self) -> FakeTyping:
        return FakeTyping(self)


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        clean_content: str,
        channel: FakeChannel,
        user: FakeDiscordUser,
    ) -> None:
        self.id = message_id
        self.clean_content = clean_content
        self.channel = channel
        self.author = FakeAuthor()
        self.mentions = [user]
        self.attachments: list[object] = []
        self.replies: list[str] = []

    async def reply(self, content: str, **_: object) -> None:
        self.replies.append(content)


async def _empty_attachments(*_: object) -> tuple[Any, ...]:
    return ()


async def _wait_for_calls(agent: FakeAgent, count: int) -> None:
    for _ in range(100):
        if len(agent.calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {count} agent calls")


async def _wait_for_replies(message: FakeMessage, count: int) -> None:
    for _ in range(100):
        if len(message.replies) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {count} replies")
