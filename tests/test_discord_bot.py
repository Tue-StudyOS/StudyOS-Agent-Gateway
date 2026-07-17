import asyncio
import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr

from study_discord_agent.config import Settings
from study_discord_agent.discord_bot import StudyBot


class FailingAgent:
    async def ask(self, **_: object) -> object:
        raise AssertionError("webhook publication must never call the agent")


class FakePublisher:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.mirror_ids: list[str] = []
        self.fail_first = fail_first

    async def publish_staged(self, mirror_id: str) -> object:
        self.mirror_ids.append(mirror_id)
        if self.fail_first and len(self.mirror_ids) == 1:
            raise RuntimeError("safe publication failure")
        return object()


class FakeBot:
    def __init__(self, queue: asyncio.Queue[str], publisher: FakePublisher) -> None:
        self.queue = queue
        self.github_mirror_publisher = publisher
        self.agent = FailingAgent()

    async def wait_until_ready(self) -> None:
        return None

    async def publish_notification(self, mirror_id: str) -> None:
        await self.github_mirror_publisher.publish_staged(mirror_id)

    def is_closed(self) -> bool:
        return len(self.github_mirror_publisher.mirror_ids) >= 2


@pytest.mark.asyncio
async def test_staged_publication_cannot_escape_passive_publisher() -> None:
    publisher = FakePublisher()
    bot = type(
        "Bot",
        (),
        {
            "github_mirror_publisher": publisher,
            "agent": FailingAgent(),
        },
    )()

    await StudyBot.publish_notification(bot, "mirror-one")  # type: ignore[arg-type]

    assert publisher.mirror_ids == ["mirror-one"]
    assert ".agent" not in inspect.getsource(StudyBot.publish_notification)


@pytest.mark.asyncio
async def test_worker_logs_failure_and_continues_with_next_event() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    await queue.put("one")
    await queue.put("two")
    publisher = FakePublisher(fail_first=True)
    bot = FakeBot(queue, publisher)

    await asyncio.wait_for(StudyBot._notification_worker(bot), timeout=1)  # type: ignore[arg-type]

    assert publisher.mirror_ids == ["one", "two"]
    await asyncio.wait_for(queue.join(), timeout=1)


@pytest.mark.asyncio
async def test_startup_enqueues_every_staged_or_unresolved_record() -> None:
    class FakeStore:
        def pending_publication_ids(self) -> tuple[str, ...]:
            return "pending", "cleanup"

    queue: asyncio.Queue[str] = asyncio.Queue()
    bot = type("Bot", (), {"queue": queue, "mirror_store": FakeStore()})()

    await StudyBot._enqueue_pending_publications(bot)  # type: ignore[arg-type]

    assert queue.get_nowait() == "pending"
    assert queue.get_nowait() == "cleanup"


@pytest.mark.asyncio
async def test_reconciler_requeues_pending_publications(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStore:
        def pending_publication_ids(self) -> tuple[str, ...]:
            return ("retry-me",)

    class FakeReconcilerBot:
        def __init__(self) -> None:
            self.queue: asyncio.Queue[str] = asyncio.Queue()
            self.mirror_store = FakeStore()
            self.closed_checks = 0

        async def wait_until_ready(self) -> None:
            return None

        async def _enqueue_pending_publications(self) -> None:
            await StudyBot._enqueue_pending_publications(self)  # type: ignore[arg-type]

        def is_closed(self) -> bool:
            self.closed_checks += 1
            return self.closed_checks > 1

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr("study_discord_agent.discord_bot.asyncio.sleep", no_delay)
    bot = FakeReconcilerBot()

    await StudyBot._publication_reconciler(bot)  # type: ignore[arg-type]

    assert bot.queue.get_nowait() == "retry-me"


def test_runtime_has_no_autonomous_github_agent_controls() -> None:
    settings = Settings(discord_token=SecretStr("token"))
    for obsolete in (
        "agent_auto_review_enabled",
        "discord_proactive_agent_enabled",
        "github_poll_enabled",
        "github_poll_interval_seconds",
        "github_poll_limit",
    ):
        assert not hasattr(settings, obsolete)

    import study_discord_agent.main as main

    assert main.__file__ is not None
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "run_github_triage_loop" not in source
    assert "github_poll" not in source
    assert "ProactiveMonitor" not in inspect.getsource(StudyBot)
