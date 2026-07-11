from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from pydantic import SecretStr

from study_discord_agent.config import Settings
from study_discord_agent.proactive import (
    ProactiveMonitor,
    is_group_space,
    is_high_signal_message,
    is_private_group_space,
    proactive_post_text,
)


def _monitor() -> ProactiveMonitor:
    client = SimpleNamespace(user=object())
    settings = Settings(discord_token=SecretStr("test-token"))
    return ProactiveMonitor(cast(discord.Client, client), settings, cast(Any, None))


def _message(
    content: str,
    *,
    age_seconds: int = 300,
    bot: bool = False,
    mentions: tuple[object, ...] = (),
    message_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(bot=bot),
        clean_content=content,
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        mentions=mentions,
    )


def test_only_group_channels_and_their_threads_are_proactive_spaces() -> None:
    assert is_group_space(SimpleNamespace(name="group-1-ai-tutor", parent=None))
    assert is_group_space(
        SimpleNamespace(name="debug-thread", parent=SimpleNamespace(name="group-1-ai-tutor"))
    )
    assert not is_group_space(SimpleNamespace(name="general", parent=None))
    assert not is_group_space(SimpleNamespace(name="bot-dev", parent=None))


def test_proactive_group_space_must_be_private() -> None:
    def private_permissions(_role: object) -> SimpleNamespace:
        return SimpleNamespace(view_channel=False)

    def public_permissions(_role: object) -> SimpleNamespace:
        return SimpleNamespace(view_channel=True)

    private = SimpleNamespace(
        name="group-1-ai-tutor",
        parent=None,
        guild=SimpleNamespace(default_role=object()),
        permissions_for=private_permissions,
    )
    public = SimpleNamespace(
        name="group-1-ai-tutor",
        parent=None,
        guild=SimpleNamespace(default_role=object()),
        permissions_for=public_permissions,
    )

    assert is_private_group_space(private)
    assert not is_private_group_space(public)


def test_candidate_requires_settled_unanswered_high_signal_message() -> None:
    monitor = _monitor()

    candidate = monitor.latest_recent_human_message(
        [_message("We're blocked by an auth error — any ideas?")]
    )

    assert candidate is not None
    assert monitor.latest_recent_human_message([_message("Nice, looks good")]) is None
    assert monitor.latest_recent_human_message([_message("Help?", age_seconds=30)]) is None


def test_high_signal_requires_a_failure_or_technical_question() -> None:
    assert is_high_signal_message("We're blocked by an auth error")
    assert is_high_signal_message("Why does the API return 401?")
    assert not is_high_signal_message("Are we meeting tomorrow?")
    assert not is_high_signal_message("Help?")


@pytest.mark.asyncio
async def test_actionable_message_is_rechecked_after_agent_turn() -> None:
    monitor = _monitor()
    messages = [_message("Why does the API return 401?", message_id=2)]

    async def history(*, limit: int) -> Any:
        assert limit == 20
        for message in messages:
            yield message

    def permissions_for(_role: object) -> SimpleNamespace:
        return SimpleNamespace(view_channel=False)

    channel = SimpleNamespace(
        name="group-1-api",
        parent=None,
        guild=SimpleNamespace(default_role=object()),
        permissions_for=permissions_for,
        history=history,
    )

    assert await monitor._still_actionable(cast(Any, channel), 2)  # pyright: ignore[reportPrivateUsage]
    assert not await monitor._still_actionable(cast(Any, channel), 1)  # pyright: ignore[reportPrivateUsage]


def test_recent_bot_message_suppresses_proactive_reply() -> None:
    monitor = _monitor()

    candidate = monitor.latest_recent_human_message(
        [
            _message("We're stuck on the parser error", age_seconds=300),
            _message("Earlier bot reply", age_seconds=600, bot=True),
        ]
    )

    assert candidate is None


def test_proactive_output_requires_small_strict_json_post() -> None:
    assert (
        proactive_post_text(
            '{"action":"POST","message":"That looks like a stale token cache — clear it once."}'
        )
        == "That looks like a stale token cache — clear it once."
    )
    assert proactive_post_text('{"action":"NO_ACTION"}') is None
    assert proactive_post_text("You could try clearing the cache") is None
    assert proactive_post_text('{"action":"POST","message":"```python\\nprint(1)\\n```"}') is None
    assert proactive_post_text('{"action":"POST","message":"- generic next step"}') is None
