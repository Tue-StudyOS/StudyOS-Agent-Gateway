import re
from enum import StrEnum
from typing import Any, Protocol, Self, cast
from uuid import UUID

import discord

TASK_COMPONENT_TEMPLATE = (
    r"^studyos:task:(?P<action>stop|add_context|retry|why|continue):"
    r"(?P<task_id>[0-9a-f]{32})$"
)


class DiscordTaskComponentAction(StrEnum):
    STOP = "stop"
    ADD_CONTEXT = "add_context"
    RETRY = "retry"
    WHY = "why"
    CONTINUE = "continue"


class DiscordTaskComponentController(Protocol):
    async def handle_task_action(
        self,
        action: DiscordTaskComponentAction,
        task_id: str,
        interaction: discord.Interaction,
    ) -> None: ...


class DiscordTaskActionItem(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.LayoutView]],
    template=TASK_COMPONENT_TEMPLATE,
):
    def __init__(
        self,
        item: discord.ui.Button[discord.ui.LayoutView],
        action: DiscordTaskComponentAction,
        task_id: str,
    ) -> None:
        super().__init__(item)
        self.action = action
        self.task_id = UUID(task_id).hex

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> Self:
        del interaction
        if not isinstance(item, discord.ui.Button):
            raise TypeError("StudyOS task actions must be Discord buttons")
        return cls(
            cast(discord.ui.Button[discord.ui.LayoutView], item),
            DiscordTaskComponentAction(match.group("action")),
            UUID(hex=match.group("task_id")).hex,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        controller = getattr(
            interaction.client,
            "discord_task_component_controller",
            None,
        )
        handler = getattr(controller, "handle_task_action", None)
        if not callable(handler):
            await interaction.response.send_message(
                "Task controls are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        typed = cast(DiscordTaskComponentController, controller)
        await typed.handle_task_action(self.action, self.task_id, interaction)
