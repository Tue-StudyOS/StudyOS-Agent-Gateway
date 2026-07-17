from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import discord

from study_discord_agent.github_mirror_cards import github_mirror_view
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
    GitHubMirrorRecord,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore


def _record(tmp_path: Path) -> GitHubMirrorRecord:
    event = GitHubMirrorEvent(
        delivery_id="delivery-card",
        event_name="pull_request",
        action="opened",
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=7,
        item_url="https://github.com/Tue-StudyOS/example/pull/7",
        title="Escape @everyone **markdown**",
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=("backend", "needs `review`"),
        base_ref="main",
        head_ref="feature",
        base_sha="b" * 40,
        head_sha="a" * 40,
        activity="Pull request opened",
        item_updated_at="2026-07-17T12:00:00+00:00",
    )
    store = GitHubMirrorStore(
        tmp_path / "mirrors.json",
        clock=lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
    )
    return store.upsert_event(event, guild_id=10, channel_id=20).record


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button[discord.ui.LayoutView]]:
    return [
        cast(discord.ui.Button[discord.ui.LayoutView], child)
        for child in view.walk_children()
        if isinstance(child, discord.ui.Button)
    ]


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay)
    )


def test_open_item_card_is_bounded_escaped_and_has_exact_controls(tmp_path: Path) -> None:
    record = _record(tmp_path)

    view = github_mirror_view(record)
    buttons = _buttons(view)
    controls = [button for button in buttons if button.custom_id is not None]

    assert isinstance(view, discord.ui.LayoutView)
    assert view.timeout is None
    assert view.total_children_count <= 40
    assert view.content_length() <= 4000
    assert len(buttons) == 5
    assert buttons[0].url == record.item_url
    assert [button.label for button in controls] == [
        "Review",
        "Security review",
        "Vulnerability scan",
        "Work on this",
    ]
    assert [button.custom_id for button in controls] == [
        f"studyos:github:{action}:{record.mirror_id}"
        for action in ("review", "security_review", "vulnerability_scan", "work")
    ]
    assert all(record.repository_full_name not in (button.custom_id or "") for button in controls)
    rendered = _text(view)
    assert "@\u200beveryone" in rendered
    assert "\\*\\*markdown\\*\\*" in rendered
    assert "needs \\`review\\`" in rendered


def test_closed_and_merged_cards_only_link_to_github(tmp_path: Path) -> None:
    record = _record(tmp_path)

    for state in (GitHubItemState.CLOSED, GitHubItemState.MERGED):
        view = github_mirror_view(replace(record, state=state))
        buttons = _buttons(view)
        assert len(buttons) == 1
        assert buttons[0].url == record.item_url
        assert buttons[0].custom_id is None
