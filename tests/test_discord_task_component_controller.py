from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_component_controller import (
    DiscordTaskInteractionController,
)
from study_discord_agent.discord_task_components import DiscordTaskComponentAction
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskState,
)
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"


class _Response:
    def __init__(self) -> None:
        self.done = False
        self.events: list[str] = []
        self.messages: list[tuple[str, bool]] = []
        self.modal: discord.ui.Modal | None = None

    def is_done(self) -> bool:
        return self.done

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        assert ephemeral and thinking
        self.done = True
        self.events.append("defer")

    async def send_message(
        self,
        content: str,
        *,
        ephemeral: bool,
        allowed_mentions: discord.AllowedMentions,
    ) -> None:
        del allowed_mentions
        self.done = True
        self.events.append("message")
        self.messages.append((content, ephemeral))

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.done = True
        self.events.append("modal")
        self.modal = modal


class _Followup:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send(
        self,
        content: str,
        *,
        ephemeral: bool,
        allowed_mentions: discord.AllowedMentions,
    ) -> None:
        del allowed_mentions
        self.messages.append((content, ephemeral))


class _Interaction:
    def __init__(self, *, actor_id: int = 1, message_id: int = 500) -> None:
        self.id = 900
        self.guild_id = 2
        self.channel_id = 10
        self.user = SimpleNamespace(id=actor_id)
        self.message: SimpleNamespace | None = SimpleNamespace(id=message_id)
        self.response = _Response()
        self.followup = _Followup()


class _Store:
    def __init__(self, record: DiscordTaskRecord) -> None:
        self.record = record

    def get(self, task_id: str) -> DiscordTaskRecord:
        if task_id != TASK_ID:
            raise KeyError(task_id)
        return self.record


class _Service:
    def __init__(self, record: DiscordTaskRecord) -> None:
        self.record = record
        self.stop_calls: list[int] = []
        self.retry_calls: list[int] = []
        self.steer_prompts: list[str] = []
        self.continue_prompts: list[str] = []

    def status(
        self, task_id: str, access: DiscordTaskAccess
    ) -> DiscordTaskRecord:
        assert task_id == TASK_ID
        assert access.actor_id > 0
        return self.record

    async def stop(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        del task_id, access
        self.stop_calls.append(interaction_id)
        return self.record

    async def retry(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord:
        del task_id, access
        self.retry_calls.append(interaction_id)
        return self.record

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        del task_id, access, interaction_id
        self.steer_prompts.append(request.prompt)
        return self.record

    async def continue_task(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord:
        del task_id, access, interaction_id
        self.continue_prompts.append(request.prompt)
        return self.record


async def _access(
    interaction: _Interaction, record: DiscordTaskRecord
) -> DiscordTaskAccess:
    return DiscordTaskAccess(
        actor_id=interaction.user.id,
        guild_id=record.guild_id,
        channel_id=interaction.channel_id,
        visible_channel_ids=frozenset({record.origin_channel_id, record.execution_channel_id}),
        manageable_channel_ids=frozenset({record.execution_channel_id}),
    )


def _controller(
    record: DiscordTaskRecord,
) -> tuple[DiscordTaskInteractionController, _Service]:
    service = _Service(record)
    return DiscordTaskInteractionController(
        cast(Any, _Store(record)),
        cast(Any, service),
        cast(Any, _access),
    ), service


@pytest.mark.asyncio
async def test_stop_defers_then_uses_fresh_authorized_service_action() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING), card_message_id=500
    )
    controller, service = _controller(record)
    interaction = _Interaction()

    await controller.handle_task_action(
        DiscordTaskComponentAction.STOP,
        TASK_ID,
        cast(Any, interaction),
    )

    assert interaction.response.events == ["defer"]
    assert service.stop_calls == [900]
    assert interaction.followup.messages == [("Stopping the task now.", True)]


@pytest.mark.asyncio
async def test_stale_or_foreign_card_is_rejected_without_service_action() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING), card_message_id=500
    )
    controller, service = _controller(record)
    interaction = _Interaction(message_id=999)

    await controller.handle_task_action(
        DiscordTaskComponentAction.STOP,
        TASK_ID,
        cast(Any, interaction),
    )

    assert not service.stop_calls
    assert "no longer current" in interaction.response.messages[0][0]


@pytest.mark.asyncio
async def test_add_context_opens_modal_as_first_response_and_reauthorizes_submit() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING), card_message_id=500
    )
    controller, service = _controller(record)
    interaction = _Interaction()

    await controller.handle_task_action(
        DiscordTaskComponentAction.ADD_CONTEXT,
        TASK_ID,
        cast(Any, interaction),
    )

    assert interaction.response.events == ["modal"]
    modal = interaction.response.modal
    assert modal is not None
    text_input = next(
        item
        for item in modal.walk_children()
        if isinstance(item, discord.ui.TextInput)
    )
    text_input._value = "Use the new course catalog"  # pyright: ignore[reportPrivateUsage]
    submit = _Interaction(message_id=500)
    submit.message = None
    await modal.on_submit(cast(Any, submit))

    assert submit.response.events == ["defer"]
    assert service.steer_prompts == ["Use the new course catalog"]
    assert submit.followup.messages == [("Added the new context.", True)]


@pytest.mark.asyncio
async def test_non_owner_cannot_open_context_or_continue_modal() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING), card_message_id=500
    )
    controller, service = _controller(record)
    interaction = _Interaction(actor_id=7)

    await controller.handle_task_action(
        DiscordTaskComponentAction.ADD_CONTEXT,
        TASK_ID,
        cast(Any, interaction),
    )

    assert interaction.response.modal is None
    assert not service.steer_prompts
    assert "owner" in interaction.response.messages[0][0]


@pytest.mark.asyncio
async def test_why_failure_is_safe_ephemeral_detail() -> None:
    record = replace(
        stored_record(
            TASK_ID,
            DiscordTaskState.FAILED,
            failure=DiscordTaskFailure(
                DiscordTaskFailureCategory.CONFIGURATION,
                "The gateway configuration is incomplete.",
                DiscordTaskRetryMode.NONE,
            ),
        ),
        card_message_id=500,
    )
    controller, _ = _controller(record)
    interaction = _Interaction()

    await controller.handle_task_action(
        DiscordTaskComponentAction.WHY,
        TASK_ID,
        cast(Any, interaction),
    )

    assert interaction.response.events == ["defer"]
    detail = interaction.followup.messages[0][0]
    assert "configuration" in detail
    assert "Retry is not safe" in detail
    assert TASK_ID in detail
