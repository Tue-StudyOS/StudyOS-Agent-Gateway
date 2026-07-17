from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
    title: str = "Current title",
) -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name="pull_request",
        action=action,
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=7,
        item_url="https://github.com/Tue-StudyOS/example/pull/7",
        title=title,
        state=state,
        author_login="student",
        labels=(),
        base_ref="main",
        head_ref="feature",
        base_sha="b" * 40,
        head_sha="a" * 40,
        activity=f"Pull request {action}",
        item_updated_at=NOW.isoformat(),
    )


class _Response:
    status = 500
    reason = "Server Error"
    headers: dict[str, str] = {}


class _Message:
    def __init__(
        self,
        message_id: int,
        *,
        nonce: str | int | None,
        channel: "_Channel",
    ) -> None:
        self.id = message_id
        self.nonce = nonce
        self.author = channel.guild.me
        self._channel = channel

    async def edit(self, **_: object) -> "_Message":
        return self

    async def delete(self) -> None:
        self._channel.messages.pop(self.id, None)


class _VanishingMessage(_Message):
    async def edit(self, **_: object) -> "_Message":
        self._channel.messages.pop(self.id, None)
        raise discord.NotFound(cast(Any, _Response()), "deleted during edit")


class _Channel(discord.abc.Messageable):
    def __init__(self, *, ambiguous_send: bool = False, expose_history: bool = True) -> None:
        self.id = 20
        self.type = discord.ChannelType.text
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self.messages: dict[int, _Message] = {}
        self.send_calls = 0
        self.ambiguous_send = ambiguous_send
        self.expose_history = expose_history

    async def _get_channel(self) -> "_Channel":  # pyright: ignore[reportIncompatibleMethodOverride]
        return self

    def permissions_for(self, _: object) -> object:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    async def send(self, **kwargs: object) -> _Message:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.send_calls += 1
        message = _Message(
            100 + self.send_calls,
            nonce=cast(str | int | None, kwargs.get("nonce")),
            channel=self,
        )
        self.messages[message.id] = message
        if self.ambiguous_send:
            raise discord.HTTPException(cast(Any, _Response()), "ambiguous create")
        return message

    async def fetch_message(self, message_id: int) -> _Message:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.messages[message_id]

    async def history(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, *, limit: int
    ) -> AsyncIterator[_Message]:
        if self.expose_history:
            for message in sorted(self.messages.values(), key=lambda item: item.id, reverse=True)[
                :limit
            ]:
                yield message


class _Client:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel

    def get_channel(self, _: int) -> _Channel:
        return self.channel

    async def fetch_channel(self, _: int) -> object:
        raise AssertionError("cached channel should be used")


def _publisher(
    tmp_path: Path, channel: _Channel
) -> tuple[GitHubMirrorPublisher, GitHubMirrorStore]:
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    publisher = GitHubMirrorPublisher(
        cast(Any, _Client(channel)), store, guild_id=10, channel_id=20
    )
    return publisher, store


@pytest.mark.asyncio
async def test_not_found_during_edit_clears_and_recreates_card(tmp_path: Path) -> None:
    channel = _Channel()
    publisher, store = _publisher(tmp_path, channel)
    created = await publisher.publish(_event("initial"))
    assert created.card_message_id is not None
    channel.messages[created.card_message_id] = _VanishingMessage(
        created.card_message_id,
        nonce=channel.messages[created.card_message_id].nonce,
        channel=channel,
    )

    recreated = await publisher.publish(_event("edited", action="edited", title="Updated"))

    assert channel.send_calls == 2
    assert recreated.card_message_id == 102
    assert store.get(created.mirror_id).card_message_id == 102


@pytest.mark.asyncio
async def test_ambiguous_create_adopts_bounded_nonce_match(tmp_path: Path) -> None:
    channel = _Channel(ambiguous_send=True)
    publisher, store = _publisher(tmp_path, channel)

    created = await publisher.publish(_event("initial"))

    assert channel.send_calls == 1
    assert created.card_message_id == 101
    assert store.get(created.mirror_id).card_message_id == 101
    assert isinstance(channel.messages[101].nonce, str)
    assert len(channel.messages[101].nonce) <= 25


@pytest.mark.asyncio
async def test_unresolved_ambiguous_create_is_not_resent(tmp_path: Path) -> None:
    channel = _Channel(ambiguous_send=True, expose_history=False)
    publisher, _ = _publisher(tmp_path, channel)

    with pytest.raises(discord.HTTPException):
        await publisher.publish(_event("initial"))
    restarted = GitHubMirrorPublisher(
        cast(Any, _Client(channel)),
        GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW),
        guild_id=10,
        channel_id=20,
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        await restarted.publish(_event("later", action="edited"))

    assert channel.send_calls == 1


def test_equal_timestamp_ready_state_cannot_regress_to_draft(tmp_path: Path) -> None:
    store = GitHubMirrorStore(tmp_path / "states.json", clock=lambda: NOW)
    draft = store.upsert_event(
        _event("draft", state=GitHubItemState.DRAFT, title="Draft"),
        guild_id=10,
        channel_id=20,
    ).record
    ready = store.upsert_event(
        _event("ready", action="ready_for_review", title="Ready"),
        guild_id=10,
        channel_id=20,
    ).record
    delayed = store.upsert_event(
        _event("delayed", state=GitHubItemState.DRAFT, title="Stale draft"),
        guild_id=10,
        channel_id=20,
    ).record

    assert draft.state is GitHubItemState.DRAFT
    assert ready.state is GitHubItemState.OPEN
    assert delayed.state is GitHubItemState.OPEN
    assert delayed.title == "Ready"
