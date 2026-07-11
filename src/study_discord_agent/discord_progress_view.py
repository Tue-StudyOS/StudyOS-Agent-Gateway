import logging
from collections.abc import Awaitable, Callable

import discord

StopHandler = Callable[[], Awaitable[bool]]
logger = logging.getLogger(__name__)


class _StopTaskButton(discord.ui.Button[discord.ui.LayoutView]):
    def __init__(self, owner_id: int, on_stop: StopHandler) -> None:
        super().__init__(
            label="Stop task",
            style=discord.ButtonStyle.danger,
            custom_id=f"studyos:stop:{owner_id}",
        )
        self._owner_id = owner_id
        self._on_stop = on_stop
        self._stopping = False

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message(
                "Only the person who started this task can stop it.",
                ephemeral=True,
            )
            return
        if self._stopping:
            await interaction.response.send_message("Already stopping it.", ephemeral=True)
            return
        self._stopping = True
        self.disabled = True
        self.label = "Stopping…"
        await interaction.response.edit_message(
            view=self.view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            stopped = await self._on_stop()
        except Exception:
            logger.exception("Discord Stop task interaction failed")
            self._stopping = False
            self.disabled = False
            self.label = "Stop task"
            await interaction.edit_original_response(
                view=self.view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.followup.send(
                "Couldn't stop it just now. The error was logged.",
                ephemeral=True,
            )
            return
        message = "Stopping it now." if stopped else "That task has already finished."
        await interaction.followup.send(message, ephemeral=True)


class DiscordProgressView(discord.ui.LayoutView):
    def __init__(self, content: str, owner_id: int, on_stop: StopHandler) -> None:
        super().__init__(timeout=None)
        self._text = discord.ui.TextDisplay[discord.ui.LayoutView](content)
        self._stop_button = _StopTaskButton(owner_id, on_stop)
        action_row = discord.ui.ActionRow[discord.ui.LayoutView](self._stop_button)
        self._container = discord.ui.Container[discord.ui.LayoutView](
            self._text,
            action_row,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(self._container)

    def update_content(self, content: str) -> None:
        self._text.content = content

    def mark_failed(self, content: str) -> None:
        self._text.content = content
        self._container.accent_color = discord.Color.red()
        self._stop_button.disabled = True

    def close(self) -> None:
        self.stop()
