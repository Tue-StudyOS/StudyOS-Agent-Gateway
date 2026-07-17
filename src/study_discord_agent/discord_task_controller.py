from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import discord
from discord import app_commands

from study_discord_agent.discord_message_context import origin_context_from_message
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_access import resolve_task_access
from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_inputs import (
    StagedDiscordAttachments,
    stage_message_attachments,
)
from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_threads import (
    DedicatedTaskThread,
    DiscordTaskThreadError,
    channel_context,
    create_dedicated_thread,
    delete_dedicated_thread,
    interaction_scope,
)

StageAttachments = Callable[..., Awaitable[StagedDiscordAttachments]]


class DiscordTaskCommandError(RuntimeError):
    pass


class _TaskStore(Protocol):
    def get(self, task_id: str) -> DiscordTaskRecord: ...

    def records(self) -> tuple[DiscordTaskRecord, ...]: ...


class _TaskService(Protocol):
    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord: ...

    def status(self, task_id: str, access: DiscordTaskAccess) -> DiscordTaskRecord: ...

    async def forget(
        self,
        task_id: str,
        access: DiscordTaskAccess,
        interaction_id: int,
    ) -> None: ...


class DiscordTaskController:
    def __init__(
        self,
        *,
        store: _TaskStore,
        service: _TaskService,
        attachment_root: Path,
        stage_attachments: StageAttachments = stage_message_attachments,
    ) -> None:
        self._store = store
        self._service = service
        self._attachment_root = attachment_root
        self._stage_attachments = stage_attachments

    async def start_slash(
        self,
        interaction: discord.Interaction,
        prompt: str,
        dedicated_thread: bool,
    ) -> DiscordTaskRecord:
        guild_id, channel_id, owner_id = _scope(interaction)
        origin_channel_id = channel_id
        execution_channel_id = channel_id
        origin_context = channel_context(interaction.channel)
        dedicated: DedicatedTaskThread | None = None
        if dedicated_thread:
            try:
                dedicated = await create_dedicated_thread(interaction)
            except DiscordTaskThreadError as error:
                raise DiscordTaskCommandError(str(error)) from error
            execution_channel_id = dedicated.id
            origin_context = DiscordOriginContext(
                channel_id=dedicated.id,
                channel_name=dedicated.name,
                channel_type="Thread",
                thread_id=dedicated.id,
                thread_name=dedicated.name,
                parent_channel_id=origin_channel_id,
                parent_channel_name=getattr(interaction.channel, "name", None),
                category_id=dedicated.category_id,
            )
        request = DiscordTaskRequest(
            source_kind=DiscordTaskSourceKind.SLASH,
            guild_id=guild_id,
            origin_channel_id=origin_channel_id,
            execution_channel_id=execution_channel_id,
            owner_id=owner_id,
            trigger_event_id=interaction.id,
            source_message_id=None,
            prompt=prompt,
            source_label="Slash command",
            attachments=_empty_attachments(),
            origin_context=origin_context,
        )
        try:
            return await self._service.start(request)
        except BaseException:
            if dedicated is not None:
                try:
                    await delete_dedicated_thread(dedicated)
                except DiscordTaskThreadError as cleanup_error:
                    raise DiscordTaskCommandError(str(cleanup_error)) from cleanup_error
            raise

    async def start_message_context(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        instruction: str,
    ) -> DiscordTaskRecord:
        guild_id, channel_id, owner_id = _scope(interaction)
        if (
            message.guild is None
            or message.guild.id != guild_id
            or message.channel.id != channel_id
        ):
            raise DiscordTaskCommandError(
                "The selected message is no longer available in this channel."
            )
        staged = await self._stage_attachments(
            message,
            self._attachment_root,
            trigger_event_id=interaction.id,
        )
        delegated = False
        try:
            request = DiscordTaskRequest(
                source_kind=DiscordTaskSourceKind.CONTEXT_ACTION,
                guild_id=guild_id,
                origin_channel_id=channel_id,
                execution_channel_id=channel_id,
                owner_id=owner_id,
                trigger_event_id=interaction.id,
                source_message_id=message.id,
                prompt=instruction,
                source_label="Message context action",
                attachments=staged,
                origin_context=origin_context_from_message(message),
            )
            delegated = True
            return await self._service.start(request)
        finally:
            if not delegated:
                staged.cleanup()

    async def status(
        self,
        interaction: discord.Interaction,
        task_id: str,
    ) -> tuple[DiscordTaskRecord, DiscordTaskAccess]:
        try:
            record = self._store.get(task_id)
        except KeyError as error:
            raise DiscordTaskCommandError("That task is no longer available.") from error
        access = await resolve_task_access(interaction, record)
        return self._service.status(task_id, access), access

    async def visible_tasks(
        self,
        interaction: discord.Interaction,
        *,
        scope: str,
        state: str,
    ) -> tuple[DiscordTaskRecord, ...]:
        if scope not in {"mine", "channel"} or state not in {
            "all",
            "active",
            "terminal",
        }:
            raise DiscordTaskCommandError("The task list filters are invalid.")
        actor_id = getattr(interaction.user, "id", None)
        records: list[DiscordTaskRecord] = []
        for record in sorted(
            self._store.records(),
            key=lambda item: (item.created_at, item.task_id),
            reverse=True,
        ):
            if scope == "mine" and record.owner_id != actor_id:
                continue
            if scope == "channel" and record.execution_channel_id != interaction.channel_id:
                continue
            if state == "active" and record.state not in ACTIVE_STATES:
                continue
            if state == "terminal" and record.state in ACTIVE_STATES:
                continue
            try:
                await resolve_task_access(interaction, record)
            except PermissionError:
                continue
            records.append(record)
            if len(records) == 10:
                break
        return tuple(records)

    async def autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        needle = current.casefold().strip()
        records = await self.visible_tasks(interaction, scope="channel", state="all")
        choices: list[app_commands.Choice[str]] = []
        for record in records:
            short_id = record.task_id.replace("-", "")[:8]
            label = f"{short_id} · {record.state.value} · {record.source_label}"
            if needle and needle not in label.casefold() and needle not in record.task_id:
                continue
            choices.append(app_commands.Choice(name=label[:100], value=record.task_id))
            if len(choices) == 10:
                break
        return choices

    async def forget(
        self,
        interaction: discord.Interaction,
        task_id: str,
    ) -> None:
        record, access = await self.status(interaction, task_id)
        if record.state in ACTIVE_STATES:
            raise DiscordTaskCommandError("An active task cannot be forgotten.")
        await self._service.forget(task_id, access, interaction.id)


def _scope(interaction: discord.Interaction) -> tuple[int, int, int]:
    try:
        return interaction_scope(interaction)
    except DiscordTaskThreadError as error:
        raise DiscordTaskCommandError(str(error)) from error


def _empty_attachments() -> StagedDiscordAttachments:
    return StagedDiscordAttachments(paths=(), directory=None)
