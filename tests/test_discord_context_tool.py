from pathlib import Path

import pytest

from study_discord_agent.discord_context_tool import fetch_context, render_messages
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.prompt_context import build_agent_prompt


def test_render_messages_formats_context_for_agent_reading() -> None:
    output = render_messages(
        123,
        [
            {
                "id": "1",
                "timestamp": "2026-05-09T18:07:00+00:00",
                "author": {"username": "Sebastian"},
                "content": "Can you brainstorm?",
            },
            {
                "id": "2",
                "timestamp": "2026-05-09T18:08:00+00:00",
                "author": {"username": "StudyOS Bot", "bot": True},
                "content": "Here are feature directions.",
            },
        ],
    )

    assert "Discord context for channel 123" in output
    assert "Sebastian: Can you brainstorm?" in output
    assert "StudyOS Bot bot: Here are feature directions." in output


def test_render_messages_includes_thread_parent_context() -> None:
    output = render_messages(
        456,
        [],
        DiscordOriginContext(
            channel_id=456,
            channel_name="PR #21 Review",
            channel_type="public_thread",
            thread_id=456,
            thread_name="PR #21 Review",
            parent_channel_id=123,
            parent_channel_name="group-4-service-aggregation",
            category_id=99,
            category_name="Textkanäle",
        ),
    )

    assert "Thread: PR #21 Review (id=456)" in output
    assert "Parent channel: group-4-service-aggregation (id=123)" in output
    assert "Category: Textkanäle (id=99)" in output


def test_build_agent_prompt_includes_discord_origin_context(tmp_path: Path) -> None:
    prompt = build_agent_prompt(
        "please check this",
        "student",
        456,
        str(tmp_path),
        789,
        origin_context=DiscordOriginContext(
            channel_id=456,
            channel_name="PR #21 Review",
            channel_type="public_thread",
            thread_id=456,
            thread_name="PR #21 Review",
            parent_channel_id=123,
            parent_channel_name="group-4-service-aggregation",
        ),
    )

    assert "Discord origin context:" in prompt
    assert "Channel: PR #21 Review (id=456, type=public_thread)" in prompt
    assert "Parent channel: group-4-service-aggregation (id=123)" in prompt


@pytest.mark.asyncio
async def test_fetch_context_requires_token() -> None:
    with pytest.raises(RuntimeError, match="DISCORD_TOKEN"):
        await fetch_context(
            channel_id=123,
            limit=20,
            before_message_id=None,
            around_message_id=None,
            token=None,
        )
