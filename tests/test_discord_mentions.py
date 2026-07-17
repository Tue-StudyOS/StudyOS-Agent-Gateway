from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import discord
import pytest

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_mentions import DiscordMentionCoordinator
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import DiscordTaskRecord, DiscordTaskState
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"


class FakeService:
    def __init__(self, active: DiscordTaskRecord | None = None) -> None:
        self.active = active
        self.starts: list[DiscordTaskRequest] = []
        self.steers: list[tuple[str, DiscordTaskAccess, DiscordTaskSteerRequest, int]] = []
        self.stops: list[tuple[str, DiscordTaskAccess, int]] = []

    def active_task(self, execution_channel_id: int) -> DiscordTaskRecord | None:
        if self.active and self.active.execution_channel_id == execution_channel_id:
            return self.active
        return None

    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord:
        self.starts.append(request)
        self.active = stored_record(
            TASK_ID,
            DiscordTaskState.STARTING,
            channel_id=request.execution_channel_id,
            owner_id=request.owner_id,
        )
        return self.active

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        self.steers.append((task_id, access, request, interaction_id))
        assert self.active is not None
        return self.active

    async def stop(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        self.stops.append((task_id, access, interaction_id))
        assert self.active is not None
        return self.active


class FakeMessage:
    def __init__(self, message_id: int, *, author_id: int = 42) -> None:
        self.id = message_id
        self.guild = type("Guild", (), {"id": 2})()
        self.channel = type("Channel", (), {"id": 10})()
        self.author = type("Author", (), {"id": author_id})()
        self.attachments: list[object] = []
        self.replies: list[tuple[str, dict[str, object]]] = []

    async def reply(self, content: str, **kwargs: object) -> None:
        self.replies.append((content, kwargs))


class AttachmentStager:
    def __init__(self) -> None:
        self.calls: list[tuple[object, Path, int]] = []

    async def __call__(
        self,
        message: object,
        root: Path,
        *,
        trigger_event_id: int,
    ) -> StagedDiscordAttachments:
        self.calls.append((message, root, trigger_event_id))
        return StagedDiscordAttachments(paths=(root / "input.txt",), directory=root)


def _record(*, owner_id: int = 42, card_message_id: int = 99) -> DiscordTaskRecord:
    return replace(
        stored_record(
            TASK_ID,
            DiscordTaskState.RUNNING,
            channel_id=10,
            owner_id=owner_id,
        ),
        card_message_id=card_message_id,
    )


def _coordinator(
    service: FakeService,
    stager: AttachmentStager,
) -> DiscordMentionCoordinator:
    return DiscordMentionCoordinator(
        cast(Any, service),
        Path("/tmp/studyos-inputs"),
        stage_attachments=stager,
    )


def _origin() -> DiscordOriginContext:
    return DiscordOriginContext(channel_id=10, channel_name="course-chat")


@pytest.mark.asyncio
async def test_mention_stages_input_and_starts_through_task_service() -> None:
    service = FakeService()
    stager = AttachmentStager()
    coordinator = _coordinator(service, stager)
    message = FakeMessage(101)
    origin = _origin()

    handled = await coordinator.dispatch(
        cast(discord.Message, message),
        "explain the proof",
        origin,
    )

    assert handled
    assert stager.calls == [(message, Path("/tmp/studyos-inputs"), 101)]
    request = service.starts[0]
    assert request.guild_id == 2
    assert request.origin_channel_id == request.execution_channel_id == 10
    assert request.owner_id == 42
    assert request.trigger_event_id == request.source_message_id == 101
    assert request.prompt == "explain the proof"
    assert request.origin_context is origin
    assert request.attachments.paths == (Path("/tmp/studyos-inputs/input.txt"),)


@pytest.mark.asyncio
async def test_unmentioned_owner_followup_preserves_origin_and_steers() -> None:
    service = FakeService(_record())
    stager = AttachmentStager()
    coordinator = _coordinator(service, stager)
    message = FakeMessage(102)
    origin = _origin()

    handled = await coordinator.dispatch(
        cast(discord.Message, message),
        "use the second lemma",
        origin,
        start_if_idle=False,
    )

    assert handled
    task_id, access, request, interaction_id = service.steers[0]
    assert task_id == TASK_ID
    assert access.actor_id == 42
    assert access.visible_channel_ids == frozenset({10})
    assert request.prompt == "use the second lemma"
    assert request.source_message_id == interaction_id == 102
    assert request.origin_context is origin
    assert service.starts == []


@pytest.mark.asyncio
async def test_other_users_mention_gets_active_card_guidance_only() -> None:
    service = FakeService(_record(owner_id=42))
    stager = AttachmentStager()
    coordinator = _coordinator(service, stager)
    message = FakeMessage(103, author_id=77)

    handled = await coordinator.dispatch(
        cast(discord.Message, message),
        "stop working",
        _origin(),
    )

    assert handled
    assert service.starts == []
    assert service.steers == []
    assert service.stops == []
    assert stager.calls == []
    content, kwargs = message.replies[0]
    assert "https://discord.com/channels/2/10/99" in content
    assert "new thread" in content.lower()
    allowed_mentions = kwargs["allowed_mentions"]
    assert isinstance(allowed_mentions, discord.AllowedMentions)
    assert allowed_mentions.everyone is False


@pytest.mark.asyncio
async def test_owner_text_stop_uses_task_service_without_staging() -> None:
    service = FakeService(_record())
    stager = AttachmentStager()
    coordinator = _coordinator(service, stager)
    message = FakeMessage(104)

    handled = await coordinator.dispatch(
        cast(discord.Message, message),
        "stop working",
        _origin(),
        start_if_idle=False,
    )

    assert handled
    task_id, access, interaction_id = service.stops[0]
    assert (task_id, access.actor_id, interaction_id) == (TASK_ID, 42, 104)
    assert stager.calls == []
    assert message.replies[0][0] == "Stopped the active task in this channel."


@pytest.mark.asyncio
async def test_duplicate_message_and_unmentioned_idle_chat_are_ignored() -> None:
    service = FakeService()
    stager = AttachmentStager()
    coordinator = _coordinator(service, stager)
    message = FakeMessage(105)

    assert not await coordinator.dispatch(
        cast(discord.Message, message),
        "ambient chat",
        _origin(),
        start_if_idle=False,
    )
    assert not await coordinator.dispatch(
        cast(discord.Message, message),
        "now mentioned",
        _origin(),
    )

    assert service.starts == []
    assert stager.calls == []


@pytest.mark.asyncio
async def test_attachment_staging_error_is_reported_without_starting() -> None:
    service = FakeService()

    async def fail_staging(
        message: object,
        root: Path,
        *,
        trigger_event_id: int,
    ) -> StagedDiscordAttachments:
        del message, root, trigger_event_id
        raise AgentWorkspaceOrAttachmentError("Attachment could not be staged safely")

    coordinator = DiscordMentionCoordinator(
        cast(Any, service),
        Path("/tmp/studyos-inputs"),
        stage_attachments=fail_staging,
    )
    message = FakeMessage(106)

    handled = await coordinator.dispatch(
        cast(discord.Message, message),
        "inspect the attachment",
        _origin(),
    )

    assert handled
    assert service.starts == []
    assert message.replies[0][0] == "Attachment could not be staged safely"
