from typing import Any, cast

import pytest

from study_discord_agent.discord_bot import StudyBot


class FakeCoordinator:
    def __init__(self, handled: bool) -> None:
        self.handled = handled
        self.calls: list[dict[str, object]] = []

    async def dispatch(
        self,
        message: object,
        prompt: str,
        origin: object,
        **kwargs: object,
    ) -> bool:
        self.calls.append({"message": message, "prompt": prompt, "origin": origin, **kwargs})
        return self.handled


class FakeUser:
    display_name = "StudyBot"


class FakeAuthor:
    bot = False


class FakeMessage:
    def __init__(self, content: str, *, mentioned: bool) -> None:
        self.clean_content = content
        self.author = FakeAuthor()
        self.channel = type(
            "Channel",
            (),
            {"id": 123, "name": "bot-dev", "category_id": None, "category": None},
        )()
        self.mentions = [FakeUser()] if mentioned else []
        self.id = 456
        self.replies: list[str] = []

    async def reply(self, content: str) -> None:
        self.replies.append(content)


def _bot(coordinator: FakeCoordinator) -> Any:
    return type(
        "Bot",
        (),
        {
            "settings": type("Settings", (), {"discord_message_agent_enabled": True})(),
            "user": FakeUser(),
            "_mentions": coordinator,
        },
    )()


@pytest.mark.asyncio
async def test_mention_can_start_a_task() -> None:
    coordinator = FakeCoordinator(True)
    bot = _bot(coordinator)
    message = FakeMessage("@StudyBot do the thing", mentioned=True)
    message.mentions = [bot.user]

    await StudyBot.on_message(bot, cast(Any, message))

    assert coordinator.calls[0]["prompt"] == "do the thing"
    assert coordinator.calls[0]["start_if_idle"] is True


@pytest.mark.asyncio
async def test_unmentioned_message_is_followup_only() -> None:
    coordinator = FakeCoordinator(False)
    bot = _bot(coordinator)
    message = FakeMessage("ambient chat", mentioned=False)

    await StudyBot.on_message(bot, cast(Any, message))

    assert coordinator.calls[0]["prompt"] == "ambient chat"
    assert coordinator.calls[0]["start_if_idle"] is False
    assert message.replies == []
