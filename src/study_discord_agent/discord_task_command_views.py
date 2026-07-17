from collections.abc import Awaitable, Callable
from typing import Protocol

import discord

PromptSubmitter = Callable[[discord.Interaction, str], Awaitable[None]]


class _ForgetController(Protocol):
    async def forget(
        self,
        interaction: discord.Interaction,
        task_id: str,
    ) -> None: ...


class DiscordTaskPromptModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        label: str,
        submit: PromptSubmitter,
    ) -> None:
        super().__init__(title=title, timeout=600)
        self._submit = submit
        self._instruction = discord.ui.TextInput[discord.ui.Modal](
            label=label,
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=4_000,
            required=True,
        )
        self.add_item(self._instruction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit(interaction, self._instruction.value.strip())


class DiscordTaskForgetView(discord.ui.View):
    def __init__(
        self,
        controller: _ForgetController,
        task_id: str,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=180)
        self._controller = controller
        self._task_id = task_id
        self._owner_id = owner_id

    @discord.ui.button(label="Forget local record", style=discord.ButtonStyle.danger)
    async def request_forget(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiscordTaskForgetView"],
    ) -> None:
        if interaction.user.id != self._owner_id:
            await _send(interaction, "Only the task owner may forget this record.")
            return
        await interaction.response.send_message(
            "Forget this local task record? Discord messages and the Codex session stay intact.",
            ephemeral=True,
            view=_ConfirmForgetView(
                self._controller,
                self._task_id,
                self._owner_id,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class _ConfirmForgetView(discord.ui.View):
    def __init__(
        self,
        controller: _ForgetController,
        task_id: str,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=60)
        self._controller = controller
        self._task_id = task_id
        self._owner_id = owner_id

    @discord.ui.button(label="Confirm forget", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["_ConfirmForgetView"],
    ) -> None:
        if interaction.user.id != self._owner_id:
            await _send(interaction, "Only the task owner may forget this record.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self._controller.forget(interaction, self._task_id)
        except (KeyError, PermissionError, RuntimeError):
            await interaction.followup.send(
                "This task record could not be forgotten safely.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.followup.send(
            "The local task record was forgotten.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _send(interaction: discord.Interaction, message: str) -> None:
    await interaction.response.send_message(
        message,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
