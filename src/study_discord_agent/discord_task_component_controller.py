import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import discord

from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAction,
    DiscordTaskAuthorizationError,
    authorize,
)
from study_discord_agent.discord_task_component_modal import (
    DiscordTaskInstructionModal,
)
from study_discord_agent.discord_task_components import DiscordTaskComponentAction
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
)
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskActionUnavailable,
    DiscordTaskServiceClosed,
)

logger = logging.getLogger(__name__)
AccessResolver = Callable[
    [discord.Interaction, DiscordTaskRecord], Awaitable[DiscordTaskAccess]
]


class _TaskStore(Protocol):
    def get(self, task_id: str) -> DiscordTaskRecord: ...


class _TaskService(Protocol):
    def status(self, task_id: str, access: DiscordTaskAccess) -> DiscordTaskRecord: ...

    async def stop(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord: ...

    async def retry(
        self, task_id: str, access: DiscordTaskAccess, interaction_id: int
    ) -> DiscordTaskRecord: ...

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord: ...

    async def continue_task(
        self,
        parent_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord: ...


class DiscordTaskInteractionController:
    def __init__(
        self,
        store: _TaskStore,
        service: _TaskService,
        resolve_access: AccessResolver,
    ) -> None:
        self._store = store
        self._service = service
        self._resolve_access = resolve_access

    async def handle_task_action(
        self,
        action: DiscordTaskComponentAction,
        task_id: str,
        interaction: discord.Interaction,
    ) -> None:
        try:
            record = self._store.get(task_id)
            _validate_card_interaction(record, interaction)
        except (KeyError, DiscordTaskAuthorizationError) as error:
            await _respond_error(interaction, str(error))
            return
        if action in {
            DiscordTaskComponentAction.ADD_CONTEXT,
            DiscordTaskComponentAction.CONTINUE,
        }:
            if interaction.user.id != record.owner_id:
                await _respond_error(interaction, "Only the task owner may use this action.")
                return
            await interaction.response.send_modal(
                DiscordTaskInstructionModal(self.submit_instruction, action, record)
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            access = await self._resolve_access(interaction, record)
            record = self._service.status(task_id, access)
            _validate_card_interaction(record, interaction)
            message = await self._perform_immediate(action, record, access, interaction.id)
        except (KeyError, DiscordTaskAuthorizationError, DiscordTaskActionUnavailable) as error:
            await _respond_error(interaction, _public_error(error))
            return
        except DiscordTaskServiceClosed:
            await _respond_error(interaction, "Task controls are shutting down. Try again later.")
            return
        except Exception:
            logger.exception("Discord task component action failed task_id=%s", task_id)
            await _respond_error(interaction, "That task action failed safely. Try again later.")
            return
        await _respond(interaction, message)

    async def _perform_immediate(
        self,
        action: DiscordTaskComponentAction,
        record: DiscordTaskRecord,
        access: DiscordTaskAccess,
        interaction_id: int,
    ) -> str:
        if action is DiscordTaskComponentAction.STOP:
            await self._service.stop(record.task_id, access, interaction_id)
            return "Stopping the task now."
        if action is DiscordTaskComponentAction.RETRY:
            await self._service.retry(record.task_id, access, interaction_id)
            return "Retry started safely."
        if action is DiscordTaskComponentAction.WHY:
            authorize(record, DiscordTaskAction.WHY_FAILED, access)
            return _failure_detail(record)
        raise DiscordTaskActionUnavailable("This task action is unavailable.")

    async def submit_instruction(
        self,
        action: DiscordTaskComponentAction,
        task_id: str,
        expected_card_id: int,
        prompt: str,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record = self._store.get(task_id)
            _validate_modal_submit(record, interaction, expected_card_id)
            access = await self._resolve_access(interaction, record)
            record = self._service.status(task_id, access)
            _validate_modal_submit(record, interaction, expected_card_id)
            if action is DiscordTaskComponentAction.ADD_CONTEXT:
                request = DiscordTaskSteerRequest(
                    prompt=prompt,
                    source_message_id=None,
                    attachments=_empty_attachments(),
                    origin_context=DiscordOriginContext(record.execution_channel_id),
                )
                await self._service.steer(task_id, access, request, interaction.id)
                message = "Added the new context."
            elif action is DiscordTaskComponentAction.CONTINUE:
                request = DiscordTaskRequest(
                    source_kind=DiscordTaskSourceKind.CONTINUATION,
                    guild_id=record.guild_id,
                    origin_channel_id=record.origin_channel_id,
                    execution_channel_id=record.execution_channel_id,
                    owner_id=record.owner_id,
                    trigger_event_id=interaction.id,
                    source_message_id=None,
                    prompt=prompt,
                    source_label="Continuation",
                    attachments=_empty_attachments(),
                    origin_context=DiscordOriginContext(record.execution_channel_id),
                )
                await self._service.continue_task(
                    task_id, access, request, interaction.id
                )
                message = "Continuation started."
            else:
                raise DiscordTaskActionUnavailable("This modal action is unavailable.")
        except (KeyError, DiscordTaskAuthorizationError, DiscordTaskActionUnavailable) as error:
            await _respond_error(interaction, _public_error(error))
            return
        except DiscordTaskServiceClosed:
            await _respond_error(interaction, "Task controls are shutting down. Try again later.")
            return
        except Exception:
            logger.exception("Discord task modal action failed task_id=%s", task_id)
            await _respond_error(interaction, "That task action failed safely. Try again later.")
            return
        await _respond(interaction, message)


def _validate_card_interaction(
    record: DiscordTaskRecord, interaction: discord.Interaction
) -> None:
    message = interaction.message
    if (
        interaction.guild_id != record.guild_id
        or interaction.channel_id != record.execution_channel_id
        or message is None
        or message.id != record.card_message_id
    ):
        raise DiscordTaskAuthorizationError("This task card is no longer current.")


def _validate_modal_submit(
    record: DiscordTaskRecord,
    interaction: discord.Interaction,
    expected_card_id: int,
) -> None:
    if (
        interaction.guild_id != record.guild_id
        or interaction.channel_id != record.execution_channel_id
        or interaction.user.id != record.owner_id
        or record.card_message_id != expected_card_id
    ):
        raise DiscordTaskAuthorizationError("This task action is no longer authorized.")


def _failure_detail(record: DiscordTaskRecord) -> str:
    failure = record.failure
    if failure is None:
        raise DiscordTaskActionUnavailable("No failure detail is available for this task.")
    kept = (
        "The saved session and partial work were kept."
        if failure.retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION
        else "No resumable session is available."
    )
    retry = (
        "Retry is safe using the saved session or cached delivery."
        if failure.retry_mode is not DiscordTaskRetryMode.NONE
        else "Retry is not safe or available automatically."
    )
    return (
        f"**Why it failed**\nCategory: `{failure.category.value}`\n"
        f"{_safe(failure.summary)}\n{kept}\n{retry}\nTask ID: `{record.task_id}`"
    )


def _safe(value: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def _empty_attachments() -> StagedDiscordAttachments:
    return StagedDiscordAttachments(paths=(), directory=None)


def _public_error(error: Exception) -> str:
    text = str(error)
    return text if text else "That task action is unavailable."


async def _respond(interaction: discord.Interaction, message: str) -> None:
    await interaction.followup.send(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _respond_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await _respond(interaction, message)
        return
    await interaction.response.send_message(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
