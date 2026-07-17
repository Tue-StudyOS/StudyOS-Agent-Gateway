from collections.abc import Awaitable, Callable

import discord

from study_discord_agent.discord_task_components import DiscordTaskComponentAction
from study_discord_agent.discord_task_model import DiscordTaskRecord

InstructionSubmitter = Callable[
    [
        DiscordTaskComponentAction,
        str,
        int,
        str,
        discord.Interaction,
    ],
    Awaitable[None],
]


class DiscordTaskInstructionModal(discord.ui.Modal):
    def __init__(
        self,
        submit: InstructionSubmitter,
        action: DiscordTaskComponentAction,
        record: DiscordTaskRecord,
    ) -> None:
        title = "Add context" if action is DiscordTaskComponentAction.ADD_CONTEXT else "Continue"
        super().__init__(title=title, timeout=600)
        self._submit = submit
        self._action = action
        self._task_id = record.task_id
        if record.card_message_id is None:
            raise ValueError("A task card is required before opening its control modal")
        self._card_message_id = record.card_message_id
        self._instructions = discord.ui.TextInput[discord.ui.Modal](
            label="Instructions",
            style=discord.TextStyle.paragraph,
            placeholder="Describe what StudyOS should do next",
            min_length=1,
            max_length=4_000,
            required=True,
        )
        self.add_item(self._instructions)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit(
            self._action,
            self._task_id,
            self._card_message_id,
            self._instructions.value.strip(),
            interaction,
        )
