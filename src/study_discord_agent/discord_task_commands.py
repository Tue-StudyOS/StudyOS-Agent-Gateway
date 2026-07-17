import logging

import discord
from discord import app_commands

from study_discord_agent.discord_task_auth import DiscordTaskAuthorizationError
from study_discord_agent.discord_task_command_views import (
    DiscordTaskForgetView,
    DiscordTaskPromptModal,
)
from study_discord_agent.discord_task_controller import (
    DiscordTaskCommandError,
    DiscordTaskController,
)
from study_discord_agent.discord_task_model import ACTIVE_STATES, DiscordTaskRecord
from study_discord_agent.discord_task_service_errors import DiscordTaskActionUnavailable

logger = logging.getLogger(__name__)
DISCORD_MESSAGE_LIMIT = 2_000


class StudyCommandGroup(
    app_commands.Group,
    name="study",
    description="Start and manage StudyOS tasks",
    guild_only=True,
):
    def __init__(self, controller: DiscordTaskController) -> None:
        super().__init__()
        self._controller = controller

    @app_commands.command(name="ask", description="Ask StudyOS to work on a task")
    @app_commands.describe(prompt="What StudyOS should do")
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str | None = None,
    ) -> None:
        if prompt is None or not prompt.strip():
            async def submit(
                submitted: discord.Interaction,
                instruction: str,
            ) -> None:
                await self._start_slash(submitted, instruction)

            await interaction.response.send_modal(
                DiscordTaskPromptModal(
                    title="Ask StudyOS",
                    label="Task",
                    submit=submit,
                )
            )
            return
        await self._start_slash(interaction, prompt.strip())

    @app_commands.command(name="tasks", description="List recent visible StudyOS tasks")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="My tasks", value="mine"),
            app_commands.Choice(name="This channel", value="channel"),
        ],
        state=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Active", value="active"),
            app_commands.Choice(name="Finished", value="terminal"),
        ],
    )
    async def tasks(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] | None = None,
        state: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            records = await self._controller.visible_tasks(
                interaction,
                scope=scope.value if scope else "mine",
                state=state.value if state else "all",
            )
        except Exception as error:
            await _safe_error(interaction, error, operation="list")
            return
        await _followup(interaction, _task_list_text(records))

    @app_commands.command(name="status", description="Show one StudyOS task")
    @app_commands.describe(task="Task ID")
    async def status(self, interaction: discord.Interaction, task: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record, _access = await self._controller.status(interaction, task)
        except Exception as error:
            await _safe_error(interaction, error, operation="status")
            return
        view = (
            DiscordTaskForgetView(self._controller, record.task_id, record.owner_id)
            if record.owner_id == interaction.user.id and record.state not in ACTIVE_STATES
            else None
        )
        if view is None:
            await _followup(interaction, _status_text(record))
        else:
            await interaction.followup.send(
                _status_text(record),
                ephemeral=True,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @status.autocomplete("task")
    async def status_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            return await self._controller.autocomplete(interaction, current)
        except Exception:
            logger.info("Discord task autocomplete failed safely")
            return []

    async def _start_slash(
        self,
        interaction: discord.Interaction,
        prompt: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record = await self._controller.start_slash(
                interaction,
                prompt,
            )
        except Exception as error:
            await _safe_error(interaction, error, operation="start")
            return
        await _followup(
            interaction,
            f"Started StudyOS task `{_short_id(record)}` in "
            f"<#{record.execution_channel_id}>.",
        )


def create_message_context_menu(
    controller: DiscordTaskController,
) -> app_commands.ContextMenu:
    async def callback(
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        async def submit(
            submitted: discord.Interaction,
            instruction: str,
        ) -> None:
            await submitted.response.defer(ephemeral=True, thinking=True)
            try:
                record = await controller.start_message_context(
                    submitted,
                    message,
                    instruction,
                )
            except Exception as error:
                await _safe_error(submitted, error, operation="context start")
                return
            await _followup(
                submitted,
                f"Started StudyOS task `{_short_id(record)}` from that message in "
                f"<#{record.execution_channel_id}>.",
            )

        await interaction.response.send_modal(
            DiscordTaskPromptModal(
                title="Ask StudyOS about this",
                label="What should StudyOS do?",
                submit=submit,
            )
        )

    guild_callback = app_commands.guild_only(callback)
    return app_commands.ContextMenu(
        name="Ask StudyOS about this",
        callback=guild_callback,
        type=discord.AppCommandType.message,
    )


def _task_line(record: DiscordTaskRecord) -> str:
    label = _safe(record.source_label)
    link = _record_link(record)
    suffix = f" · [Open]({link})" if link is not None else ""
    return f"- `{_short_id(record)}` · **{record.state.value}** · {label}{suffix}"


def _task_list_text(records: tuple[DiscordTaskRecord, ...]) -> str:
    if not records:
        return "No matching visible tasks."
    lines: list[str] = []
    for index, record in enumerate(records):
        line = _task_line(record)
        candidate = "\n".join((*lines, line))
        remaining = len(records) - index
        suffix = f"-# … {remaining} more matching task{'s' if remaining != 1 else ''}."
        if len(candidate) <= DISCORD_MESSAGE_LIMIT:
            lines.append(line)
            continue
        bounded = "\n".join((*lines, suffix))
        if len(bounded) <= DISCORD_MESSAGE_LIMIT:
            lines.append(suffix)
        break
    return "\n".join(lines)


def _status_text(record: DiscordTaskRecord) -> str:
    lines = [
        f"**Task `{_short_id(record)}`**",
        f"State: `{record.state.value}` · attempt {record.attempt}",
        f"Source: {_safe(record.source_label)}",
        f"Full task ID: `{record.task_id}`",
    ]
    if record.failure is not None:
        summary = _safe(record.failure.summary)
        lines.extend(
            (
                f"Failure: `{record.failure.category.value}`",
                summary,
                f"Retry mode: `{record.failure.retry_mode.value}`",
            )
        )
    return "\n".join(lines)


async def _safe_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    operation: str,
) -> None:
    if isinstance(error, (DiscordTaskCommandError, DiscordTaskActionUnavailable)):
        message = str(error)
    elif isinstance(error, (DiscordTaskAuthorizationError, KeyError)):
        message = "That task is unavailable or no longer visible to you."
    else:
        logger.exception("Discord task command failed operation=%s", operation)
        message = "That StudyOS task action failed safely. Try again later."
    await _followup(interaction, message)


async def _followup(interaction: discord.Interaction, message: str) -> None:
    await interaction.followup.send(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _short_id(record: DiscordTaskRecord) -> str:
    return record.task_id.replace("-", "")[:8]


def _safe(value: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def _record_link(record: DiscordTaskRecord) -> str | None:
    message_id = record.card_message_id or record.source_message_id
    if message_id is None:
        return None
    channel_id = (
        record.execution_channel_id
        if record.card_message_id is not None
        else record.origin_channel_id
    )
    return f"https://discord.com/channels/{record.guild_id}/{channel_id}/{message_id}"
