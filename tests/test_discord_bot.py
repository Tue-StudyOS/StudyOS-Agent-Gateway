import asyncio
import inspect
from datetime import UTC, datetime

import pytest

from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
)


def _event(delivery: str) -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery,
        event_name="issues",
        action="opened",
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.ISSUE,
        item_number=1,
        item_url="https://github.com/Tue-StudyOS/example/issues/1",
        title="Question",
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=(),
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity="Issue opened",
        item_updated_at=datetime(2026, 7, 17, tzinfo=UTC).isoformat(),
    )


class FailingAgent:
    async def ask(self, **_: object) -> object:
        raise AssertionError("webhook publication must never call the agent")


class FakePublisher:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.events: list[GitHubMirrorEvent] = []
        self.fail_first = fail_first

    async def publish(self, event: GitHubMirrorEvent) -> object:
        self.events.append(event)
        if self.fail_first and len(self.events) == 1:
            raise RuntimeError("safe publication failure")
        return object()


class FakeBot:
    def __init__(self, queue: asyncio.Queue[GitHubMirrorEvent], publisher: FakePublisher) -> None:
        self.queue = queue
        self.github_mirror_publisher = publisher
        self.agent = FailingAgent()
        self.settings = type("Settings", (), {"agent_auto_review_enabled": True})()

    async def wait_until_ready(self) -> None:
        return None

    async def publish_notification(self, event: GitHubMirrorEvent) -> None:
        await self.github_mirror_publisher.publish(event)

    def is_closed(self) -> bool:
        return len(self.github_mirror_publisher.events) >= 2


@pytest.mark.asyncio
async def test_stale_auto_review_env_cannot_escape_passive_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_AUTO_REVIEW_ENABLED", "true")
    publisher = FakePublisher()
    bot = type(
        "Bot",
        (),
        {
            "github_mirror_publisher": publisher,
            "agent": FailingAgent(),
            "settings": type("Settings", (), {"agent_auto_review_enabled": True})(),
        },
    )()

    await StudyBot.publish_notification(bot, _event("one"))  # type: ignore[arg-type]

    assert publisher.events == [_event("one")]
    assert ".agent" not in inspect.getsource(StudyBot.publish_notification)


@pytest.mark.asyncio
async def test_worker_logs_failure_and_continues_with_next_event() -> None:
    queue: asyncio.Queue[GitHubMirrorEvent] = asyncio.Queue()
    await queue.put(_event("one"))
    await queue.put(_event("two"))
    publisher = FakePublisher(fail_first=True)
    bot = FakeBot(queue, publisher)

    await asyncio.wait_for(StudyBot._notification_worker(bot), timeout=1)  # type: ignore[arg-type]

    assert publisher.events == [_event("one"), _event("two")]
    await asyncio.wait_for(queue.join(), timeout=1)
