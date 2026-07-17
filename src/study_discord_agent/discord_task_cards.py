from datetime import datetime
from typing import cast
from uuid import UUID

import discord

from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.discord_task_components import (
    DiscordTaskActionItem,
    DiscordTaskComponentAction,
)
from study_discord_agent.discord_task_model import (
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskState,
)
from study_discord_agent.discord_task_service_errors import DiscordTaskControlState

MAX_CARD_TEXT = 3_900
MAX_PROGRESS_STEPS = 6
_FAILURE_STATES = {
    DiscordTaskState.DELIVERY_FAILED,
    DiscordTaskState.FAILED,
    DiscordTaskState.INTERRUPTED,
    DiscordTaskState.TIMED_OUT,
}

_STATE_LABELS = {
    DiscordTaskState.RECOVERING: "Recovering",
    DiscordTaskState.STARTING: "Starting",
    DiscordTaskState.RUNNING: "Working",
    DiscordTaskState.STOPPING: "Stopping",
    DiscordTaskState.DELIVERING: "Delivering result",
    DiscordTaskState.COMPLETED: "Completed",
    DiscordTaskState.DELIVERY_FAILED: "Delivery failed",
    DiscordTaskState.FAILED: "Failed",
    DiscordTaskState.TIMED_OUT: "Timed out",
    DiscordTaskState.STOPPED: "Stopped",
    DiscordTaskState.INTERRUPTED: "Interrupted",
}


def build_task_card(
    record: DiscordTaskRecord,
    progress: AgentProgress | None,
    controls: DiscordTaskControlState,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    content = discord.ui.TextDisplay[discord.ui.LayoutView](
        _card_text(record, progress)
    )
    buttons = _buttons(record, controls)
    children: list[discord.ui.Item[discord.ui.LayoutView]] = [content]
    if buttons:
        children.append(discord.ui.ActionRow[discord.ui.LayoutView](*buttons))
    container = discord.ui.Container[discord.ui.LayoutView](
        *children,
        accent_color=_accent(record.state),
    )
    view.add_item(container)
    return view


def _card_text(record: DiscordTaskRecord, progress: AgentProgress | None) -> str:
    created_at = int(datetime.fromisoformat(record.created_at).timestamp())
    lines = [
        f"### {_STATE_LABELS[record.state]}",
        (
            f"Task `{UUID(record.task_id).hex[:8]}` · <@{record.owner_id}> · "
            f"started <t:{created_at}:R> · attempt {record.attempt}"
        ),
        f"-# {_safe(record.source_label)}",
    ]
    if progress is not None and record.state in {
        DiscordTaskState.RECOVERING,
        DiscordTaskState.STARTING,
        DiscordTaskState.RUNNING,
        DiscordTaskState.STOPPING,
        DiscordTaskState.DELIVERING,
    }:
        lines.extend(_progress_lines(progress))
    if record.state in _FAILURE_STATES and record.failure is not None:
        lines.extend(("", f"**What happened:** {_safe(record.failure.summary)}"))
        if record.failure.retry_mode is DiscordTaskRetryMode.NONE:
            lines.append("-# Automatic retry is unavailable for this task.")
    if record.state is DiscordTaskState.COMPLETED and record.result_message_id is not None:
        lines.extend(("", "The result was delivered successfully."))
    if record.state is DiscordTaskState.STOPPED:
        lines.extend(("", "The task was stopped before result delivery."))
    return "\n".join(lines)[:MAX_CARD_TEXT]


def _progress_lines(progress: AgentProgress) -> list[str]:
    lines: list[str] = []
    if progress.plan:
        lines.extend(("", "**Plan**"))
        markers = {"completed": "[x]", "inProgress": "[>]", "pending": "[ ]"}
        for step in progress.plan[:MAX_PROGRESS_STEPS]:
            lines.append(f"`{markers.get(step.status, '[ ]')}` {_safe(step.step)}")
        remaining = len(progress.plan) - MAX_PROGRESS_STEPS
        if remaining > 0:
            lines.append(f"-# … {remaining} later step{'s' if remaining != 1 else ''}")
    if progress.now:
        lines.extend(("", f"-# Now: {_safe(progress.now)}"))
    if progress.completed:
        lines.append(f"-# Completed: {_safe(progress.completed)}")
    if progress.next_step:
        lines.append(f"-# Next: {_safe(progress.next_step)}")
    return lines


def _buttons(
    record: DiscordTaskRecord,
    controls: DiscordTaskControlState,
) -> list[discord.ui.Item[discord.ui.LayoutView]]:
    task_id = UUID(record.task_id).hex
    buttons: list[discord.ui.Item[discord.ui.LayoutView]] = []
    if record.source_message_id is not None:
        buttons.append(
            discord.ui.Button[discord.ui.LayoutView](
                label="View request",
                style=discord.ButtonStyle.link,
                url=_message_url(
                    record,
                    record.source_message_id,
                    channel_id=record.origin_channel_id,
                ),
            )
        )
    if record.state in {
        DiscordTaskState.RECOVERING,
        DiscordTaskState.STARTING,
        DiscordTaskState.RUNNING,
        DiscordTaskState.STOPPING,
    }:
        buttons.append(
            _action(
                "Stop task",
                DiscordTaskComponentAction.STOP,
                task_id,
                discord.ButtonStyle.danger,
            )
        )
    if record.state is DiscordTaskState.RUNNING and controls.steering:
        buttons.append(
            _action("Add context", DiscordTaskComponentAction.ADD_CONTEXT, task_id)
        )
    if record.state is DiscordTaskState.COMPLETED:
        if record.result_message_id is not None:
            buttons.append(
                discord.ui.Button[discord.ui.LayoutView](
                    label="View result",
                    style=discord.ButtonStyle.link,
                    url=_message_url(record, record.result_message_id),
                )
            )
        if controls.continuable:
            buttons.append(
                _action("Continue", DiscordTaskComponentAction.CONTINUE, task_id)
            )
    if record.state in _FAILURE_STATES and record.failure is not None:
        buttons.append(
            _action("Why it failed", DiscordTaskComponentAction.WHY, task_id)
        )
        retry_mode = record.failure.retry_mode
        if retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY or (
            retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION and controls.resumable
        ):
            buttons.append(
                _action(
                    "Retry",
                    DiscordTaskComponentAction.RETRY,
                    task_id,
                    discord.ButtonStyle.primary,
                )
            )
    return buttons


def _action(
    label: str,
    action: DiscordTaskComponentAction,
    task_id: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
) -> discord.ui.Item[discord.ui.LayoutView]:
    return cast(
        discord.ui.Item[discord.ui.LayoutView],
        DiscordTaskActionItem(
            discord.ui.Button[discord.ui.LayoutView](
                label=label,
                style=style,
                custom_id=f"studyos:task:{action.value}:{task_id}",
            ),
            action,
            task_id,
        ),
    )


def _message_url(
    record: DiscordTaskRecord,
    message_id: int,
    *,
    channel_id: int | None = None,
) -> str:
    return (
        f"https://discord.com/channels/{record.guild_id}/"
        f"{channel_id or record.execution_channel_id}/{message_id}"
    )


def _safe(value: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def _accent(state: DiscordTaskState) -> discord.Color:
    if state is DiscordTaskState.COMPLETED:
        return discord.Color.green()
    if state in {
        DiscordTaskState.FAILED,
        DiscordTaskState.TIMED_OUT,
        DiscordTaskState.DELIVERY_FAILED,
        DiscordTaskState.INTERRUPTED,
    }:
        return discord.Color.red()
    if state in {DiscordTaskState.STOPPING, DiscordTaskState.STOPPED}:
        return discord.Color.dark_grey()
    return discord.Color.blurple()
