from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.github_mirror_cards import github_mirror_view
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
)
from study_discord_agent.github_mirror_publisher import (
    GitHubMirrorChannelAccessError,
    GitHubMirrorConfigurationError,
    GitHubMirrorPublisher,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _event(delivery: str = "delivery-1", *, title: str = "Title") -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery,
        event_name="issues",
        action="opened",
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.ISSUE,
        item_number=12,
        item_url="https://github.com/Tue-StudyOS/example/issues/12",
        title=title,
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=("question",),
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity="Issue opened",
        item_updated_at=NOW.isoformat(),
    )


class FakeResponse:
    status = 404
    reason = "Not Found"
    headers: dict[str, str] = {}


class ForbiddenResponse:
    status = 403
    reason = "Forbidden"
    headers: dict[str, str] = {}


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        nonce: str | None,
        channel: "FakeChannel",
        view: discord.ui.LayoutView | None = None,
    ) -> None:
        self.id = message_id
        self.nonce = nonce
        self.author = channel.guild.me
        self.channel = channel
        self.current_view = view
        self.edits: list[dict[str, object]] = []
        self.deleted = False

    async def edit(self, **kwargs: object) -> "FakeMessage":
        self.edits.append(kwargs)
        view = kwargs.get("view")
        if isinstance(view, discord.ui.LayoutView):
            self.current_view = view
        return self

    async def delete(self) -> None:
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeChannel(discord.abc.Messageable):
    def __init__(self, *, permissions: object | None = None) -> None:
        self.id = 20
        self.type = discord.ChannelType.text
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self._permissions = permissions or SimpleNamespace(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
        self.messages: dict[int, FakeMessage] = {}
        self.sent: list[tuple[FakeMessage, dict[str, object]]] = []
        self.fetch_error: BaseException | None = None

    async def _get_channel(self) -> "FakeChannel":  # pyright: ignore[reportIncompatibleMethodOverride]
        return self

    def permissions_for(self, _: object) -> object:
        return self._permissions

    async def send(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        content: str | None = None,
        *,
        nonce: str | int | None = None,
        view: discord.ui.LayoutView | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> FakeMessage:
        kwargs: dict[str, object] = {
            "content": content,
            "nonce": nonce,
            "view": view,
            "allowed_mentions": allowed_mentions,
        }
        message = FakeMessage(
            100 + len(self.sent),
            nonce=cast(str | None, nonce),
            channel=self,
            view=view,
        )
        self.messages[message.id] = message
        self.sent.append((message, kwargs))
        return message

    def seed_history(
        self,
        *,
        nonce: str | None = None,
        view: discord.ui.LayoutView | None = None,
    ) -> FakeMessage:
        message = FakeMessage(
            100 + len(self.sent),
            nonce=nonce,
            channel=self,
            view=view,
        )
        self.messages[message.id] = message
        self.sent.append((message, {"nonce": nonce, "view": view}))
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.fetch_error is not None:
            raise self.fetch_error
        if message_id not in self.messages:
            raise discord.NotFound(cast(Any, FakeResponse()), "missing")
        return self.messages[message_id]

    async def history(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, *, limit: int | None
    ) -> AsyncIterator[FakeMessage]:
        candidates = self.sent if limit is None else self.sent[-limit:]
        for message, _ in tuple(reversed(candidates)):
            if message.id in self.messages:
                yield message


class FakeClient:
    def __init__(self, channel: object | None) -> None:
        self.channel = channel
        self.fetches = 0

    def get_channel(self, _: int) -> object | None:
        return self.channel

    async def fetch_channel(self, _: int) -> object:
        self.fetches += 1
        if self.channel is None:
            raise discord.NotFound(cast(Any, FakeResponse()), "missing")
        return self.channel


def _store(tmp_path: Path) -> GitHubMirrorStore:
    return GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)


def _publisher(
    tmp_path: Path, channel: object | None, *, channel_id: int | None = 20
) -> tuple[GitHubMirrorPublisher, GitHubMirrorStore]:
    store = _store(tmp_path)
    return (
        GitHubMirrorPublisher(
            cast(Any, FakeClient(channel)),
            store,
            guild_id=10,
            channel_id=channel_id,
        ),
        store,
    )


@pytest.mark.asyncio
async def test_publish_creates_one_card_then_edits_same_logical_item(tmp_path: Path) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)

    created = await publisher.publish(_event())
    updated_event = replace(
        _event("delivery-2", title="Updated"),
        item_updated_at=(NOW + timedelta(seconds=1)).isoformat(),
    )
    updated = await publisher.publish(updated_event)
    duplicate = await publisher.publish(updated_event)

    assert len(channel.sent) == 1
    assert created.card_message_id == updated.card_message_id == duplicate.card_message_id
    assert store.get(created.mirror_id).title == "Updated"
    assert not duplicate.publication_pending
    assert store.pending_publication_ids() == ()
    assert len(channel.messages[cast(int, created.card_message_id)].edits) == 3
    send_kwargs = channel.sent[0][1]
    allowed = cast(discord.AllowedMentions, send_kwargs["allowed_mentions"])
    assert allowed.everyone is False and allowed.users is False and allowed.roles is False
    assert send_kwargs["content"] is None


@pytest.mark.asyncio
async def test_missing_card_is_recreated_once_but_ambiguous_fetch_is_not(tmp_path: Path) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)
    first = await publisher.publish(_event())
    assert first.card_message_id is not None
    del channel.messages[first.card_message_id]

    recreated = await publisher.publish(
        replace(_event("delivery-2"), item_updated_at=(NOW + timedelta(seconds=1)).isoformat())
    )

    assert len(channel.sent) == 2
    assert recreated.card_message_id == 101
    channel.fetch_error = discord.HTTPException(cast(Any, FakeResponse()), "ambiguous")
    with pytest.raises(discord.HTTPException):
        await publisher.publish(
            replace(
                _event("delivery-3"),
                item_updated_at=(NOW + timedelta(seconds=2)).isoformat(),
            )
        )
    assert len(channel.sent) == 2
    assert store.get(first.mirror_id).card_message_id == 101


@pytest.mark.asyncio
async def test_revoked_channel_access_is_a_typed_failure_without_recreation(
    tmp_path: Path,
) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)
    record = await publisher.publish(_event())
    channel.fetch_error = discord.Forbidden(cast(Any, ForbiddenResponse()), "denied")

    with pytest.raises(GitHubMirrorChannelAccessError):
        await publisher.publish(
            replace(
                _event("delivery-2"),
                item_updated_at=(NOW + timedelta(seconds=1)).isoformat(),
            )
        )

    assert len(channel.sent) == 1
    assert store.get(record.mirror_id).card_message_id == record.card_message_id


@pytest.mark.asyncio
async def test_create_race_deletes_orphan_card(tmp_path: Path) -> None:
    class RacingStore(GitHubMirrorStore):
        def attach_card_if_missing(  # type: ignore[no-untyped-def]
            self, mirror_id: str, message_id: int, creation_nonce: str
        ):
            channel.messages[777] = FakeMessage(
                777,
                nonce=creation_nonce,
                channel=channel,
            )
            current = self.get(mirror_id)
            winner = self.compare_and_set(
                mirror_id,
                current.revision,
                lambda record: replace(
                    record,
                    card_message_id=777,
                    card_create_pending=False,
                    card_create_nonce=None,
                    card_cleanup_nonce=creation_nonce,
                ),
            )
            return winner, False

    channel = FakeChannel()
    store = RacingStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    publisher = GitHubMirrorPublisher(
        cast(Any, FakeClient(channel)), store, guild_id=10, channel_id=20
    )

    record = await publisher.publish(_event())

    assert record.card_message_id == 777
    assert channel.sent[0][0].deleted


@pytest.mark.asyncio
async def test_precommit_attach_failure_deletes_new_orphan(tmp_path: Path) -> None:
    class FailingStore(GitHubMirrorStore):
        def attach_card_if_missing(  # type: ignore[no-untyped-def]
            self, mirror_id: str, message_id: int, creation_nonce: str
        ):
            raise OSError("store unavailable")

    channel = FakeChannel()
    store = FailingStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    publisher = GitHubMirrorPublisher(
        cast(Any, FakeClient(channel)), store, guild_id=10, channel_id=20
    )

    with pytest.raises(OSError, match="store unavailable"):
        await publisher.publish(_event())

    assert channel.sent[0][0].deleted


@pytest.mark.asyncio
async def test_crash_recovery_scans_complete_history_for_persisted_marker(
    tmp_path: Path,
) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)
    staged = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    claimed, claim_won = store.claim_card_creation(staged.mirror_id)
    assert claim_won and claimed.card_create_nonce is not None

    original = channel.seed_history(nonce=None, view=github_mirror_view(claimed))
    for _ in range(101):
        channel.seed_history()
    sent_before_recovery = len(channel.sent)

    recovered = await publisher.publish(_event())

    assert len(channel.sent) == sent_before_recovery
    assert recovered.card_message_id == original.id
    assert store.get(staged.mirror_id).card_create_pending is False


@pytest.mark.asyncio
async def test_crash_recovery_durably_removes_all_duplicate_marker_cards(
    tmp_path: Path,
) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)
    staged = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    claimed, _ = store.claim_card_creation(staged.mirror_id)

    canonical = channel.seed_history(nonce=None, view=github_mirror_view(claimed))
    for _ in range(101):
        channel.seed_history()
    duplicate = channel.seed_history(nonce=None, view=github_mirror_view(claimed))

    recovered = await publisher.publish(_event())

    assert recovered.card_message_id == canonical.id
    assert duplicate.deleted
    assert GitHubMirrorStore(
        tmp_path / "mirrors.json", clock=lambda: NOW
    ).get(staged.mirror_id).card_cleanup_nonce is None


@pytest.mark.asyncio
async def test_staged_record_can_be_published_without_in_memory_event(tmp_path: Path) -> None:
    channel = FakeChannel()
    publisher, store = _publisher(tmp_path, channel)
    staged = store.upsert_event(_event(), guild_id=10, channel_id=20).record

    published = await publisher.publish_staged(staged.mirror_id)

    assert published.card_message_id is not None
    assert not published.publication_pending
    assert store.pending_publication_ids() == ()


@pytest.mark.asyncio
async def test_inaccessible_channel_fails_after_staging_but_missing_config_cannot_stage(
    tmp_path: Path,
) -> None:
    publisher, store = _publisher(tmp_path, FakeChannel(), channel_id=None)
    with pytest.raises(GitHubMirrorConfigurationError):
        await publisher.publish(_event())
    assert store.records() == ()

    denied = SimpleNamespace(
        view_channel=True,
        send_messages=False,
        read_message_history=True,
    )
    publisher, store = _publisher(tmp_path, FakeChannel(permissions=denied))
    with pytest.raises(GitHubMirrorChannelAccessError):
        await publisher.publish(_event("delivery-denied"))
    assert len(store.records()) == 1
    assert store.records()[0].publication_pending


def test_publisher_module_has_no_execution_or_github_write_dependency() -> None:
    import study_discord_agent.github_mirror_publisher as module

    assert module.__file__ is not None
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("AgentGateway", "DiscordTaskService", "GitHubClient", ".agent.ask"):
        assert forbidden not in source
