from datetime import UTC, datetime
from typing import Any

import pytest

from study_discord_agent.proactive import ProactiveMonitor


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ask(self, prompt: str, user: str, channel_id: int | None) -> object:
        self.calls.append({"prompt": prompt, "user": user, "channel_id": channel_id})
        return type("Reply", (), {"message": "done"})()


class FakeClient:
    def __init__(self) -> None:
        self.user = object()
        self.active_channels: set[int] = set()

    def has_active_mention_task(self, channel_id: int) -> bool:
        return channel_id in self.active_channels


class FakeSettings:
    discord_proactive_recent_activity_seconds = 1800
    discord_proactive_dry_run = False


class FakeAuthor:
    bot = False

    def __str__(self) -> str:
        return "student"


class FakeMessage:
    def __init__(self, message_id: int, content: str, mentions: list[object] | None = None) -> None:
        self.id = message_id
        self.clean_content = content
        self.author = FakeAuthor()
        self.created_at = datetime.now(UTC)
        self.mentions = mentions or []


class FakeChannel:
    def __init__(self, channel_id: int, messages: list[FakeMessage]) -> None:
        self.id = channel_id
        self.messages = messages
        self.sent: list[str] = []

    def history(self, limit: int) -> object:
        del limit
        return self._iter_messages()

    async def _iter_messages(self) -> object:
        for message in self.messages:
            yield message

    async def send(self, text: str) -> object:
        self.sent.append(text)
        return type("SentMessage", (), {"id": 999})()


@pytest.mark.asyncio
async def test_proactive_skips_latest_message_that_mentions_bot() -> None:
    client = FakeClient()
    agent = FakeAgent()
    monitor = ProactiveMonitor(client, FakeSettings(), agent)  # type: ignore[arg-type]
    channel = FakeChannel(123, [FakeMessage(1, "@StudyOS Bot please help", [client.user])])

    await monitor.check_channel(channel)  # type: ignore[arg-type]
    await monitor.check_channel(channel)  # type: ignore[arg-type]

    assert agent.calls == []
    assert channel.sent == []


@pytest.mark.asyncio
async def test_proactive_skips_channel_with_active_mention_task() -> None:
    client = FakeClient()
    client.active_channels.add(123)
    agent = FakeAgent()
    monitor = ProactiveMonitor(client, FakeSettings(), agent)  # type: ignore[arg-type]
    channel = FakeChannel(123, [FakeMessage(1, "follow-up without direct mention")])

    await monitor.check_channel(channel)  # type: ignore[arg-type]

    assert agent.calls == []
    assert channel.sent == []

    client.active_channels.clear()
    await monitor.check_channel(channel)  # type: ignore[arg-type]

    assert len(agent.calls) == 1
    assert channel.sent == ["done"]
