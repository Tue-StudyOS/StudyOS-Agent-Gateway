import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.discord_task_model import DiscordTaskIntent
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_service_state import new_record
from study_discord_agent.discord_task_store import DiscordTaskStore
from study_discord_agent.github_mirror_action_store import GitHubActionReservation
from study_discord_agent.github_mirror_controller import GitHubMirrorController
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorEvent,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


class _Permissions:
    view_channel = True
    create_public_threads = True
    send_messages_in_threads = True


class _Thread:
    type = discord.ChannelType.public_thread
    locked = False
    category_id = None

    def __init__(self) -> None:
        self.id = 100
        self.name = "issue-12-studyos"
        self.parent_id = 20

    def permissions_for(self, _: object) -> _Permissions:
        return _Permissions()


class _Channel:
    id = 20
    name = "pr-review"
    type = discord.ChannelType.text

    def permissions_for(self, _: object) -> _Permissions:
        return _Permissions()


class _Card:
    id = 100

    def __init__(self, client: "_Client", channel: _Channel) -> None:
        self.channel = channel
        self._client = client
        self.create_calls = 0

    async def create_thread(self, **_: object) -> _Thread:
        self.create_calls += 1
        self._client.thread = _Thread()
        return self._client.thread


class _Response:
    def __init__(self) -> None:
        self.modal: discord.ui.Modal | None = None
        self.messages: list[str] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.modal = modal
        self._done = True

    async def defer(self, **_: object) -> None:
        self._done = True

    async def send_message(self, content: str, **_: object) -> None:
        self.messages.append(content)
        self._done = True


class _Followup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str, **_: object) -> None:
        self.messages.append(content)


class _Interaction:
    def __init__(self, card: _Card | None, *, actor_id: int = 1, event_id: int = 900) -> None:
        self.id = event_id
        self.guild_id = 10
        self.channel_id = 20
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self.channel = card.channel if card is not None else _Channel()
        self.user = SimpleNamespace(id=actor_id)
        self.message = card
        self.response = _Response()
        self.followup = _Followup()


class _NotFoundResponse:
    status = 404
    reason = "Not Found"
    headers: dict[str, str] = {}


class _Client:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel
        self.thread: _Thread | None = None
        self.user = SimpleNamespace(id=99)

    def get_channel(self, channel_id: int) -> object | None:
        if channel_id == 20:
            return self.channel
        return self.thread if self.thread is not None and channel_id == self.thread.id else None

    async def fetch_channel(self, channel_id: int) -> object:
        channel = self.get_channel(channel_id)
        if channel is None:
            raise discord.NotFound(cast(Any, _NotFoundResponse()), "missing")
        return channel


class _Service:
    def __init__(self, store: DiscordTaskStore) -> None:
        self.store = store
        self.requests: list[DiscordTaskRequest] = []

    async def start(self, request: DiscordTaskRequest):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        record = new_record(request, request.task_id or "", NOW)
        self.store.create(record)
        return record


@pytest.mark.asyncio
async def test_work_modal_starts_one_typed_task_and_reuses_one_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "canonical"
    repository = canonical / "example"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (repository / "README.md").write_text("example", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "init"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    mirrors = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    record = mirrors.upsert_event(
        GitHubMirrorEvent(
            delivery_id="delivery-1",
            event_name="issues",
            action="opened",
            repository_full_name="Tue-StudyOS/example",
            item_kind=GitHubItemKind.ISSUE,
            item_number=12,
            item_url="https://github.com/Tue-StudyOS/example/issues/12",
            title="Implement this",
            state=GitHubItemState.OPEN,
            author_login="student",
            labels=(),
            base_ref=None,
            head_ref=None,
            base_sha=None,
            head_sha=None,
            activity="Issue opened",
            item_updated_at=NOW.isoformat(),
        ),
        guild_id=10,
        channel_id=20,
    ).record
    claimed, _ = mirrors.claim_card_creation(record.mirror_id)
    assert claimed.card_create_nonce is not None
    mirrors.attach_card_if_missing(record.mirror_id, 100, claimed.card_create_nonce)
    tasks = DiscordTaskStore(tmp_path / "tasks.json", clock=lambda: NOW)
    service = _Service(tasks)
    channel = _Channel()
    client = _Client(channel)
    card = _Card(client, channel)
    controller = GitHubMirrorController(
        cast(Any, client), mirrors, tasks, cast(Any, service), canonical
    )

    button = _Interaction(card)
    await controller.handle_mirror_action(
        GitHubMirrorAction.WORK, record.mirror_id, cast(Any, button)
    )
    modal = button.response.modal
    assert modal is not None
    field = cast(Any, modal).children[0]
    assert (field.min_length, field.max_length) == (1, 4_000)
    field._value = "Implement the requested change"

    unauthorized = _Interaction(None, actor_id=2, event_id=901)
    await modal.on_submit(cast(Any, unauthorized))
    assert not service.requests

    actions = cast(Any, controller)._starter._actions
    original_reserve = actions.reserve

    def replace_card_during_reservation(*args: object) -> GitHubActionReservation:
        reservation = original_reserve(*args)  # type: ignore[arg-type]
        return replace(
            reservation,
            record=replace(reservation.record, card_message_id=101),
        )

    with monkeypatch.context() as race:
        race.setattr(
            actions,
            "reserve",
            replace_card_during_reservation,
        )
        replaced = _Interaction(None, event_id=902)
        await modal.on_submit(cast(Any, replaced))

    assert not service.requests
    assert card.create_calls == 0

    submit = _Interaction(None, event_id=901)
    await modal.on_submit(cast(Any, submit))
    await controller.submit_work(
        record.mirror_id,
        100,
        1,
        "Implement the requested change",
        cast(Any, card),
        cast(Any, _Interaction(None, event_id=901)),
    )

    assert card.create_calls == 1
    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.intent is DiscordTaskIntent.IMPLEMENTATION
    assert request.source_reference_id == record.mirror_id
    assert request.repository_commit_sha == commit
    assert request.task_id is not None
    assert mirrors.get(record.mirror_id).thread_id == 100
