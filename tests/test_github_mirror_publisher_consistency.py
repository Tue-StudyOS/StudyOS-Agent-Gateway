import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
)
from study_discord_agent.github_mirror_publisher import GitHubMirrorPublisher
from study_discord_agent.github_mirror_store import GitHubMirrorStore

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _event(
    delivery_id: str,
    *,
    state: GitHubItemState = GitHubItemState.OPEN,
    action: str = "opened",
    updated_at: datetime = NOW,
) -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name="issues",
        action=action,
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.ISSUE,
        item_number=12,
        item_url="https://github.com/Tue-StudyOS/example/issues/12",
        title=f"Issue {delivery_id}",
        state=state,
        author_login="student",
        labels=("question",),
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity=f"Issue {action}",
        item_updated_at=updated_at.isoformat(),
    )


def _has_controls(view: discord.ui.LayoutView) -> bool:
    return any(
        isinstance(child, discord.ui.Button) and child.custom_id is not None
        for child in view.walk_children()
    )


class CoordinatedMessage:
    def __init__(
        self,
        message_id: int,
        view: discord.ui.LayoutView,
        *,
        nonce: str,
        author: object,
    ) -> None:
        self.id = message_id
        self.nonce = nonce
        self.author = author
        self.current_view = view
        self.edits: list[discord.ui.LayoutView] = []
        self.open_edit_started = asyncio.Event()
        self.release_open_edit = asyncio.Event()
        self._blocked_open_edit = False

    async def edit(self, **kwargs: object) -> "CoordinatedMessage":
        view = cast(discord.ui.LayoutView, kwargs["view"])
        if _has_controls(view) and not self._blocked_open_edit:
            self._blocked_open_edit = True
            self.open_edit_started.set()
            await self.release_open_edit.wait()
        self.current_view = view
        self.edits.append(view)
        return self

    async def delete(self) -> None:
        raise AssertionError("the canonical card must not be deleted")


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        view: discord.ui.LayoutView,
        *,
        nonce: str,
        author: object,
    ) -> None:
        self.id = message_id
        self.nonce = nonce
        self.author = author
        self.current_view = view

    async def edit(self, **kwargs: object) -> "FakeMessage":
        self.current_view = cast(discord.ui.LayoutView, kwargs["view"])
        return self

    async def delete(self) -> None:
        return None


class FakeChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.id = 20
        self.type = discord.ChannelType.text
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self.messages: dict[int, FakeMessage | CoordinatedMessage] = {}
        self.sent: list[FakeMessage] = []

    async def _get_channel(self) -> "FakeChannel":  # pyright: ignore[reportIncompatibleMethodOverride]
        return self

    def permissions_for(self, _: object) -> object:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    async def send(self, **kwargs: object) -> FakeMessage:  # pyright: ignore[reportIncompatibleMethodOverride]
        view = cast(discord.ui.LayoutView, kwargs["view"])
        message = FakeMessage(
            100 + len(self.sent),
            view,
            nonce=cast(str, kwargs["nonce"]),
            author=self.guild.me,
        )
        self.messages[message.id] = message
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> discord.Message:
        return cast(discord.Message, self.messages[message_id])

    async def history(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, *, limit: int | None
    ) -> AsyncIterator[FakeMessage | CoordinatedMessage]:
        candidates = self.sent if limit is None else self.sent[-limit:]
        for message in tuple(reversed(candidates)):
            if message.id in self.messages:
                yield message


class FakeClient:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, _: int) -> FakeChannel:
        return self.channel

    async def fetch_channel(self, _: int) -> object:
        raise AssertionError("cached channel should be used")


def _publisher(channel: FakeChannel, store: GitHubMirrorStore) -> GitHubMirrorPublisher:
    return GitHubMirrorPublisher(
        cast(Any, FakeClient(channel)),
        store,
        guild_id=10,
        channel_id=20,
    )


@pytest.mark.asyncio
async def test_older_publisher_rerenders_latest_canonical_revision(tmp_path: Path) -> None:
    path = tmp_path / "mirrors.json"
    channel = FakeChannel()
    older_store = GitHubMirrorStore(path, clock=lambda: NOW)
    newer_store = GitHubMirrorStore(path, clock=lambda: NOW)
    older_publisher = _publisher(channel, older_store)
    newer_publisher = _publisher(channel, newer_store)
    created = await older_publisher.publish(_event("initial"))
    assert created.card_message_id is not None
    initial_message = channel.messages[created.card_message_id]
    coordinated = CoordinatedMessage(
        created.card_message_id,
        initial_message.current_view,
        nonce=initial_message.nonce,
        author=initial_message.author,
    )
    channel.messages[created.card_message_id] = coordinated

    older_task = asyncio.create_task(
        older_publisher.publish(
            _event("older", action="edited", updated_at=NOW + timedelta(seconds=1))
        )
    )
    await asyncio.wait_for(coordinated.open_edit_started.wait(), timeout=1)
    newer_result = await asyncio.wait_for(
        newer_publisher.publish(
            _event(
                "newer",
                state=GitHubItemState.CLOSED,
                action="closed",
                updated_at=NOW + timedelta(seconds=2),
            )
        ),
        timeout=1,
    )
    coordinated.release_open_edit.set()
    older_result = await asyncio.wait_for(older_task, timeout=1)

    canonical = older_store.get(created.mirror_id)
    assert canonical.state is GitHubItemState.CLOSED
    assert newer_result.revision == older_result.revision == canonical.revision
    assert not _has_controls(coordinated.current_view)
    assert len(channel.sent) == 1
