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
    updated_at: datetime = NOW,
) -> GitHubMirrorEvent:
    action = "closed" if state is GitHubItemState.CLOSED else "edited"
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
        labels=(),
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


class _Response:
    status = 500
    reason = "Server Error"
    headers: dict[str, str] = {}


class _Message:
    def __init__(
        self,
        message_id: int,
        *,
        nonce: str | None,
        view: discord.ui.LayoutView,
        channel: "_Channel",
        delete_failures: int = 0,
    ) -> None:
        self.id = message_id
        self.nonce = nonce
        self.author = channel.guild.me
        self.current_view = view
        self.channel = channel
        self.delete_failures = delete_failures

    async def edit(self, **kwargs: object) -> "_Message":
        self.current_view = cast(discord.ui.LayoutView, kwargs["view"])
        return self

    async def delete(self) -> None:
        if self.delete_failures:
            self.delete_failures -= 1
            raise discord.HTTPException(cast(Any, _Response()), "transient delete")
        self.channel.messages.pop(self.id, None)


class _Channel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.id = 20
        self.type = discord.ChannelType.text
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self.messages: dict[int, _Message] = {}
        self.sent_nonces: list[str] = []

    async def _get_channel(self) -> "_Channel":  # pyright: ignore[reportIncompatibleMethodOverride]
        return self

    def permissions_for(self, _: object) -> object:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    async def send(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        content: str | None = None,
        *,
        nonce: str | int | None = None,
        view: discord.ui.LayoutView | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> _Message:
        del content, allowed_mentions
        assert isinstance(nonce, str)
        assert view is not None
        self.sent_nonces.append(nonce)
        message = _Message(
            100 + len(self.sent_nonces),
            nonce=nonce,
            view=view,
            channel=self,
        )
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int) -> _Message:  # pyright: ignore[reportIncompatibleMethodOverride]
        try:
            return self.messages[message_id]
        except KeyError:
            raise discord.NotFound(cast(Any, _Response()), "missing") from None

    async def history(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, *, limit: int | None
    ) -> AsyncIterator[_Message]:
        messages = sorted(self.messages.values(), key=lambda item: item.id, reverse=True)
        for message in messages if limit is None else messages[:limit]:
            yield message

    def externally_delete(self, message_id: int) -> None:
        self.messages.pop(message_id)


class _Client:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel

    def get_channel(self, _: int) -> _Channel:
        return self.channel

    async def fetch_channel(self, _: int) -> object:
        raise AssertionError("cached channel should be used")


def _publisher(channel: _Channel, store: GitHubMirrorStore) -> GitHubMirrorPublisher:
    return GitHubMirrorPublisher(
        cast(Any, _Client(channel)),
        store,
        guild_id=10,
        channel_id=20,
    )


@pytest.mark.asyncio
async def test_restart_resends_persisted_pre_send_claim_with_same_nonce(tmp_path: Path) -> None:
    path = tmp_path / "mirrors.json"
    store = GitHubMirrorStore(path, clock=lambda: NOW)
    record = store.upsert_event(_event("initial"), guild_id=10, channel_id=20).record
    claimed, won = store.claim_card_creation(record.mirror_id)
    assert won
    assert claimed.card_create_nonce is not None

    channel = _Channel()
    restarted = _publisher(channel, GitHubMirrorStore(path, clock=lambda: NOW))
    published = await restarted.publish(_event("restart", updated_at=NOW + timedelta(seconds=1)))

    assert channel.sent_nonces == [claimed.card_create_nonce]
    assert published.card_message_id is not None
    assert not published.card_create_pending


@pytest.mark.asyncio
async def test_missing_card_recreation_rotates_persisted_nonce(tmp_path: Path) -> None:
    channel = _Channel()
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    publisher = _publisher(channel, store)
    created = await publisher.publish(_event("initial"))
    assert created.card_message_id is not None
    first_nonce = channel.messages[created.card_message_id].nonce
    channel.externally_delete(created.card_message_id)

    recreated = await publisher.publish(_event("recreated", updated_at=NOW + timedelta(seconds=1)))

    assert recreated.card_message_id is not None
    assert channel.messages[recreated.card_message_id].nonce != first_nonce
    assert channel.sent_nonces == [first_nonce, channel.messages[recreated.card_message_id].nonce]


@pytest.mark.asyncio
async def test_raced_attachment_reconciles_canonical_message(tmp_path: Path) -> None:
    class WinnerCrashesStore(GitHubMirrorStore):
        def attach_card_if_missing(self, mirror_id: str, message_id: int, creation_nonce: str):  # type: ignore[no-untyped-def]
            self.upsert_event(
                _event(
                    "closed",
                    state=GitHubItemState.CLOSED,
                    updated_at=NOW + timedelta(seconds=1),
                ),
                guild_id=10,
                channel_id=20,
            )
            winner, attached = super().attach_card_if_missing(mirror_id, message_id, creation_nonce)
            assert attached
            return winner, False

    channel = _Channel()
    store = WinnerCrashesStore(tmp_path / "mirrors.json", clock=lambda: NOW)

    published = await _publisher(channel, store).publish(_event("initial"))

    assert published.state is GitHubItemState.CLOSED
    assert published.card_message_id is not None
    assert not _has_controls(channel.messages[published.card_message_id].current_view)


@pytest.mark.asyncio
async def test_failed_duplicate_cleanup_is_retried_after_reconciliation(tmp_path: Path) -> None:
    path = tmp_path / "mirrors.json"
    store = GitHubMirrorStore(path, clock=lambda: NOW)
    initial = store.upsert_event(_event("initial"), guild_id=10, channel_id=20).record
    claimed, _ = store.claim_card_creation(initial.mirror_id)
    assert claimed.card_create_nonce is not None
    channel = _Channel()
    old_view = discord.ui.LayoutView(timeout=None)
    for message_id in (101, 102):
        channel.messages[message_id] = _Message(
            message_id,
            nonce=claimed.card_create_nonce,
            view=old_view,
            channel=channel,
            delete_failures=1 if message_id == 102 else 0,
        )
    publisher = _publisher(channel, GitHubMirrorStore(path, clock=lambda: NOW))

    with pytest.raises(discord.HTTPException, match="transient delete"):
        await publisher.publish(
            _event(
                "closed",
                state=GitHubItemState.CLOSED,
                updated_at=NOW + timedelta(seconds=1),
            )
        )

    retained = store.get(initial.mirror_id)
    assert retained.card_message_id == 101
    assert retained.card_cleanup_nonce == claimed.card_create_nonce
    assert not _has_controls(channel.messages[101].current_view)
    assert set(channel.messages) == {101, 102}

    recovered = await publisher.publish(
        _event(
            "retry",
            state=GitHubItemState.CLOSED,
            updated_at=NOW + timedelta(seconds=2),
        )
    )

    assert recovered.card_cleanup_nonce is None
    assert set(channel.messages) == {101}
