import asyncio
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import discord
import pytest

from study_discord_agent.agent import AgentReply
from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.discord_delivery_resources import (
    DiscordDeliveryLease,
    PinnedDiscordFile,
)
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_delivery import DiscordTaskDeliveryError
from study_discord_agent.discord_task_messenger import DiscordTaskCardMessenger
from study_discord_agent.discord_task_model import DiscordTaskRecord, DiscordTaskState
from study_discord_agent.discord_task_service_errors import DiscordTaskControlState
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"


class _Message:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edits: list[dict[str, object]] = []

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)


class _Channel:
    def __init__(self) -> None:
        self.id = 10
        self.messages: dict[int, _Message] = {}
        self.sends: list[dict[str, object]] = []
        self.send_error: BaseException | None = None
        self._next_id = 500

    async def send(self, **kwargs: object) -> _Message:
        self.sends.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        message = _Message(self._next_id)
        self._next_id += 1
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int) -> _Message:
        return self.messages[message_id]


class _Client:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> _Channel | None:
        return self.channel if channel_id == self.channel.id else None

    async def fetch_channel(self, channel_id: int) -> _Channel:
        assert channel_id == self.channel.id
        return self.channel


class _Store:
    def __init__(self, record: DiscordTaskRecord) -> None:
        self.record = record

    def get(self, task_id: str) -> DiscordTaskRecord:
        if task_id != self.record.task_id:
            raise KeyError(task_id)
        return self.record


async def _controls(_record: DiscordTaskRecord) -> DiscordTaskControlState:
    return DiscordTaskControlState(steering=True, resumable=True, continuable=True)


def _messenger(
    tmp_path: Path,
    record: DiscordTaskRecord,
    *,
    min_edit_interval_seconds: float = 0,
) -> tuple[DiscordTaskCardMessenger, _Store, _Channel]:
    channel = _Channel()
    store = _Store(record)
    messenger = DiscordTaskCardMessenger(
        client=cast(Any, _Client(channel)),
        store=store,
        resolve_controls=_controls,
        artifact_root=tmp_path,
        min_edit_interval_seconds=min_edit_interval_seconds,
    )
    return messenger, store, channel


def _rendered(view: object) -> str:
    assert isinstance(view, discord.ui.LayoutView)
    return "\n".join(
        child.content
        for child in view.walk_children()
        if isinstance(child, discord.ui.TextDisplay)
    )


def _assert_mentions_disabled(value: object) -> None:
    assert isinstance(value, discord.AllowedMentions)
    assert value.everyone is False
    assert value.users is False
    assert value.roles is False


@pytest.mark.asyncio
async def test_card_creation_and_progress_edit_use_components_v2_safely(
    tmp_path: Path,
) -> None:
    starting = stored_record(TASK_ID, DiscordTaskState.STARTING)
    messenger, store, channel = _messenger(tmp_path, starting)

    card_id = await messenger.create_card(starting)

    assert card_id == 500
    _assert_mentions_disabled(channel.sends[0]["allowed_mentions"])
    assert "Starting" in _rendered(channel.sends[0]["view"])

    store.record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        card_message_id=card_id,
    )
    sink = messenger.progress_sink(TASK_ID)
    await sink(AgentProgress(now="Running focused tests"))
    async with asyncio.timeout(0.5):
        while not channel.messages[card_id].edits:
            await asyncio.sleep(0)

    edit = channel.messages[card_id].edits[-1]
    _assert_mentions_disabled(edit["allowed_mentions"])
    assert "Running focused tests" in _rendered(edit["view"])
    await messenger.close()


@pytest.mark.asyncio
async def test_terminal_render_cancels_delayed_progress_and_stays_canonical(
    tmp_path: Path,
) -> None:
    running = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        card_message_id=500,
    )
    messenger, store, channel = _messenger(
        tmp_path,
        running,
        min_edit_interval_seconds=0.05,
    )
    channel.messages[500] = _Message(500)
    await messenger.progress_sink(TASK_ID)(AgentProgress(now="Stale progress"))
    store.record = replace(
        stored_record(TASK_ID, DiscordTaskState.COMPLETED),
        card_message_id=500,
        result_message_id=700,
    )

    await messenger.render_card(running)
    await asyncio.sleep(0.08)

    edits = channel.messages[500].edits
    assert "Completed" in _rendered(edits[-1]["view"])
    assert "Stale progress" not in _rendered(edits[-1]["view"])
    await messenger.close()


@pytest.mark.asyncio
async def test_delivery_uses_only_pinned_streams_and_non_owning_file_wrappers(
    tmp_path: Path,
) -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.DELIVERING),
        card_message_id=500,
    )
    messenger, _, channel = _messenger(tmp_path, record)
    stream = BytesIO(b"pinned result")
    missing_path = tmp_path / "must-not-be-reopened.txt"
    lease = DiscordDeliveryLease(
        files=(
            PinnedDiscordFile(
                source_path=missing_path,
                filename="result.txt",
                size=13,
                stream=stream,
            ),
        ),
        _release=lambda: None,
    )
    reply = PreparedDiscordReply(
        message="@everyone safe result",
        files=(missing_path,),
        delivery_lease=lease,
    )

    result_id = await messenger.deliver_reply(record, reply)

    assert result_id == 500
    sent_files = cast(list[discord.File], channel.sends[0]["files"])
    assert sent_files[0].fp is stream
    assert not stream.closed
    _assert_mentions_disabled(channel.sends[0]["allowed_mentions"])
    await messenger.close()
    lease.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "definitive"),
    [
        (OSError("connection reset"), False),
        (
            discord.Forbidden(
                cast(Any, type("Response", (), {"status": 403, "reason": "Forbidden"})()),
                "forbidden",
            ),
            True,
        ),
    ],
)
async def test_delivery_classifies_definitive_and_ambiguous_send_failures(
    tmp_path: Path,
    error: BaseException,
    definitive: bool,
) -> None:
    record = stored_record(TASK_ID, DiscordTaskState.DELIVERING)
    messenger, _, channel = _messenger(tmp_path, record)
    channel.send_error = error
    lease = DiscordDeliveryLease(files=(), _release=lambda: None)
    reply = PreparedDiscordReply(message="result", files=(), delivery_lease=lease)

    with pytest.raises(DiscordTaskDeliveryError) as raised:
        await messenger.deliver_reply(record, reply)

    assert raised.value.definitive_non_delivery is definitive
    await messenger.close()
    lease.close()


@pytest.mark.asyncio
async def test_prepare_reply_uses_task_scoped_artifact_name(tmp_path: Path) -> None:
    record = stored_record(TASK_ID, DiscordTaskState.RUNNING)
    messenger, _, _ = _messenger(tmp_path, record)

    prepared = await messenger.prepare_reply(
        record,
        AgentReply(message="# Detailed result\n" + "result " * 200),
    )

    assert prepared.generated_file == tmp_path / "discord-replies" / f"reply-{TASK_ID}.md"
    await messenger.close()
