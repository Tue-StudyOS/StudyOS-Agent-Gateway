import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import discord

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_mention_context import (
    active_task_guidance,
    message_scope,
    owner_access,
    public_task_error,
    reply_safely,
)
from study_discord_agent.discord_message_context import is_cancel_prompt
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAuthorizationError,
)
from study_discord_agent.discord_task_inputs import (
    StagedDiscordAttachments,
    stage_message_attachments,
)
from study_discord_agent.discord_task_model import (
    DiscordTaskRecord,
    DiscordTaskSourceKind,
)
from study_discord_agent.discord_task_request import (
    DiscordTaskRequest,
    DiscordTaskSteerRequest,
)
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskActionUnavailable,
    DiscordTaskChannelBusy,
    DiscordTaskServiceClosed,
)

logger = logging.getLogger(__name__)
MAX_SEEN_MESSAGE_IDS = 2_048
StageAttachments = Callable[..., Awaitable[StagedDiscordAttachments]]


class _TaskService(Protocol):
    def active_task(self, execution_channel_id: int) -> DiscordTaskRecord | None: ...

    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord: ...

    async def steer(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        request: DiscordTaskSteerRequest,
        interaction_id: int,
    ) -> DiscordTaskRecord: ...

    async def stop(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        interaction_id: int,
    ) -> DiscordTaskRecord: ...


class DiscordMentionCoordinator:
    """Translate Discord messages into requests for the shared task service."""

    def __init__(
        self,
        service: _TaskService,
        attachment_root: Path,
        *,
        stage_attachments: StageAttachments = stage_message_attachments,
    ) -> None:
        self._service = service
        self._attachment_root = attachment_root
        self._stage_attachments = stage_attachments
        self._seen_ids: set[int] = set()
        self._seen_order: deque[int] = deque()
        self._seen_lock = asyncio.Lock()

    async def dispatch(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
        *,
        start_if_idle: bool = True,
    ) -> bool:
        if not await self._claim_message(message.id):
            logger.info("duplicate Discord task message ignored message_id=%s", message.id)
            return False
        scope = message_scope(message)
        if scope is None:
            if start_if_idle:
                await reply_safely(
                    message,
                    "StudyOS tasks are available in server channels only.",
                )
                return True
            return False
        guild_id, channel_id, actor_id = scope
        active = self._service.active_task(channel_id)
        if active is None:
            if not start_if_idle:
                return False
            if is_cancel_prompt(prompt):
                await reply_safely(
                    message,
                    "No active task is running in this channel.",
                )
                return True
            return await self._handle_intake_error(
                message,
                self._start(
                    message,
                    prompt,
                    origin_context,
                    guild_id,
                    channel_id,
                    actor_id,
                ),
            )
        if active.owner_id != actor_id:
            if not start_if_idle:
                return False
            await reply_safely(message, active_task_guidance(active))
            return True

        access = owner_access(active, actor_id, guild_id, channel_id)
        if is_cancel_prompt(prompt):
            return await self._stop(message, active, access)
        return await self._handle_intake_error(
            message,
            self._steer(message, prompt, origin_context, active, access),
        )

    async def _start(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
        guild_id: int,
        channel_id: int,
        actor_id: int,
    ) -> bool:
        staged = await self._stage_attachments(
            message,
            self._attachment_root,
            trigger_event_id=message.id,
        )
        delegated = False
        try:
            request = DiscordTaskRequest(
                source_kind=DiscordTaskSourceKind.MENTION,
                guild_id=guild_id,
                origin_channel_id=channel_id,
                execution_channel_id=channel_id,
                owner_id=actor_id,
                trigger_event_id=message.id,
                source_message_id=message.id,
                prompt=prompt,
                source_label="Discord mention",
                attachments=staged,
                origin_context=origin_context,
            )
            delegated = True
            await self._service.start(request)
            return True
        except DiscordTaskChannelBusy:
            active = self._service.active_task(channel_id)
            if active is not None:
                await reply_safely(message, active_task_guidance(active))
                return True
            await reply_safely(
                message,
                "This channel already has an active StudyOS task.",
            )
            return True
        except _PUBLIC_ERRORS as error:
            await reply_safely(message, public_task_error(error))
            return True
        except Exception:
            logger.exception("Discord mention start failed message_id=%s", message.id)
            await reply_safely(
                message,
                "That StudyOS task could not be started safely.",
            )
            return True
        finally:
            if not delegated:
                staged.cleanup()

    async def _steer(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
        active: DiscordTaskRecord,
        access: DiscordTaskAccess,
    ) -> bool:
        staged = await self._stage_attachments(
            message,
            self._attachment_root,
            trigger_event_id=message.id,
        )
        delegated = False
        try:
            request = DiscordTaskSteerRequest(
                prompt=prompt,
                source_message_id=message.id,
                attachments=staged,
                origin_context=origin_context,
            )
            delegated = True
            await self._service.steer(active.task_id, access, request, message.id)
            return True
        except _PUBLIC_ERRORS as error:
            await reply_safely(message, public_task_error(error))
            return True
        except Exception:
            logger.exception("Discord mention steer failed task_id=%s", active.task_id)
            await reply_safely(message, "That follow-up could not be applied safely.")
            return True
        finally:
            if not delegated:
                staged.cleanup()

    async def _stop(
        self,
        message: discord.Message,
        active: DiscordTaskRecord,
        access: DiscordTaskAccess,
    ) -> bool:
        try:
            await self._service.stop(active.task_id, access, message.id)
        except _PUBLIC_ERRORS as error:
            await reply_safely(message, public_task_error(error))
        except Exception:
            logger.exception("Discord text stop failed task_id=%s", active.task_id)
            await reply_safely(message, "That task could not be stopped safely.")
        else:
            await reply_safely(message, "Stopped the active task in this channel.")
        return True

    async def _handle_intake_error(
        self,
        message: discord.Message,
        operation: Awaitable[bool],
    ) -> bool:
        try:
            return await operation
        except AgentWorkspaceOrAttachmentError as error:
            await reply_safely(message, str(error))
        except Exception:
            logger.exception("Discord message intake failed message_id=%s", message.id)
            await reply_safely(message, "That Discord input could not be handled safely.")
        return True

    async def _claim_message(self, message_id: int) -> bool:
        async with self._seen_lock:
            if message_id in self._seen_ids:
                return False
            self._seen_ids.add(message_id)
            self._seen_order.append(message_id)
            while len(self._seen_order) > MAX_SEEN_MESSAGE_IDS:
                self._seen_ids.discard(self._seen_order.popleft())
            return True


_PUBLIC_ERRORS = (
    AgentWorkspaceOrAttachmentError,
    DiscordTaskActionUnavailable,
    DiscordTaskAuthorizationError,
    DiscordTaskServiceClosed,
)
