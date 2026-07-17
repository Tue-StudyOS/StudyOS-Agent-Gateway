from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from discord import app_commands

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.discord_delivery_cache import DiscordDeliveryCache
from study_discord_agent.discord_mentions import DiscordMentionCoordinator
from study_discord_agent.discord_task_access import resolve_task_access
from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_commands import (
    StudyCommandGroup,
    create_message_context_menu,
)
from study_discord_agent.discord_task_component_controller import (
    DiscordTaskInteractionController,
)
from study_discord_agent.discord_task_components import DiscordTaskActionItem
from study_discord_agent.discord_task_controller import DiscordTaskController
from study_discord_agent.discord_task_execution import (
    DiscordTaskExecutionContextResolver,
)
from study_discord_agent.discord_task_messenger import DiscordTaskCardMessenger
from study_discord_agent.discord_task_model import DiscordTaskRecord
from study_discord_agent.discord_task_service import DiscordTaskService
from study_discord_agent.discord_task_service_errors import DiscordTaskControlState
from study_discord_agent.discord_task_store import DiscordTaskStore

if TYPE_CHECKING:
    from study_discord_agent.discord_bot import StudyBot

logger = logging.getLogger(__name__)
WaitUntilReady = Callable[[], Awaitable[None]]
AfterReconcile = Callable[[], Awaitable[None]]


class _OwnerControlResolver:
    def __init__(self) -> None:
        self._service: DiscordTaskService | None = None

    def bind(self, service: DiscordTaskService) -> None:
        if self._service is not None:
            raise RuntimeError("Discord task control resolver is already bound")
        self._service = service

    async def __call__(self, record: DiscordTaskRecord) -> DiscordTaskControlState:
        if self._service is None:
            raise RuntimeError("Discord task control resolver is not bound")
        access = DiscordTaskAccess(
            actor_id=record.owner_id,
            guild_id=record.guild_id,
            channel_id=record.execution_channel_id,
            visible_channel_ids=frozenset({record.origin_channel_id, record.execution_channel_id}),
            manageable_channel_ids=frozenset(),
        )
        return await self._service.resolve_controls(record.task_id, access)


@dataclass
class DiscordTaskApplication:
    store: DiscordTaskStore
    delivery_cache: DiscordDeliveryCache
    presentation: DiscordTaskCardMessenger
    service: DiscordTaskService
    command_controller: DiscordTaskController
    component_controller: DiscordTaskInteractionController
    mentions: DiscordMentionCoordinator
    command_group: StudyCommandGroup
    message_context_menu: app_commands.ContextMenu
    _registered: bool = field(default=False, init=False)
    _reconciliation_task: asyncio.Task[None] | None = field(default=None, init=False)
    _close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _closed: bool = field(default=False, init=False)

    def register(self, client: StudyBot) -> None:
        if self._registered:
            raise RuntimeError("Discord task application is already registered")
        client.discord_task_component_controller = self.component_controller
        client.add_dynamic_items(DiscordTaskActionItem)
        client.tree.add_command(self.command_group)
        client.tree.add_command(self.message_context_menu)
        self._registered = True

    def start_reconciliation(
        self,
        wait_until_ready: WaitUntilReady,
        after_reconcile: AfterReconcile | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("Discord task application is closed")
        if self._reconciliation_task is not None:
            return
        self._reconciliation_task = asyncio.create_task(
            self._reconcile_after_ready(wait_until_ready, after_reconcile),
            name="discord-task-reconciliation",
        )

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            reconciliation = self._reconciliation_task
            if reconciliation is not None and not reconciliation.done():
                reconciliation.cancel()
                with suppress(asyncio.CancelledError):
                    await reconciliation
            await self.service.close()
            self._closed = True

    async def _reconcile_after_ready(
        self,
        wait_until_ready: WaitUntilReady,
        after_reconcile: AfterReconcile | None,
    ) -> None:
        try:
            await wait_until_ready()
            await self.service.reconcile_startup()
            if after_reconcile is not None:
                await after_reconcile()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Discord task startup reconciliation failed")


def create_discord_task_application(
    client: StudyBot,
    settings: Settings,
    agent: AgentGateway,
    execution_context_resolver: DiscordTaskExecutionContextResolver | None = None,
) -> DiscordTaskApplication:
    allowed_roots = tuple(
        Path(root).expanduser() for root in settings.discord_artifact_allowed_root_list
    )
    if not allowed_roots:
        raise RuntimeError("DISCORD_ARTIFACT_ALLOWED_ROOTS must contain at least one path")
    attachment_root = Path(settings.discord_attachment_dir).expanduser()
    store = DiscordTaskStore(default_discord_task_store_path(settings.codex_home))
    delivery_cache = DiscordDeliveryCache()
    control_resolver = _OwnerControlResolver()
    presentation = DiscordTaskCardMessenger(
        client=client,
        store=store,
        resolve_controls=control_resolver,
        artifact_root=allowed_roots[0],
    )
    service = DiscordTaskService(
        agent=agent,
        store=store,
        presentation=presentation,
        delivery_cache=delivery_cache,
        allowed_artifact_roots=allowed_roots,
        max_artifact_bytes=settings.discord_artifact_max_bytes,
        execution_context_resolver=execution_context_resolver,
    )
    control_resolver.bind(service)
    command_controller = DiscordTaskController(
        store=store,
        service=service,
        attachment_root=attachment_root,
    )
    component_controller = DiscordTaskInteractionController(
        store,
        service,
        resolve_task_access,
    )
    mentions = DiscordMentionCoordinator(service, attachment_root)
    return DiscordTaskApplication(
        store=store,
        delivery_cache=delivery_cache,
        presentation=presentation,
        service=service,
        command_controller=command_controller,
        component_controller=component_controller,
        mentions=mentions,
        command_group=StudyCommandGroup(command_controller),
        message_context_menu=create_message_context_menu(command_controller),
    )


def default_discord_task_store_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "gateway" / "discord-tasks.json"
