from typing import Any

import pytest

from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.github_events import DiscordNotification


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ask(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        **_: object,
    ) -> object:
        self.calls.append({"prompt": prompt, "user": user, "channel_id": channel_id})
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
