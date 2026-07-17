from collections.abc import Awaitable, Callable

import discord

WorkSubmitter = Callable[
    [str, int, int, str, discord.Message, discord.Interaction],
    Awaitable[None],
]


class GitHubWorkModal(discord.ui.Modal):
    def __init__(
        self,
        submit: WorkSubmitter,
        *,
        mirror_id: str,
        card_message: discord.Message,
        actor_id: int,
    ) -> None:
        super().__init__(title="Work on this GitHub item", timeout=600)
        self._submit = submit
        self._mirror_id = mirror_id
        self._card_message = card_message
        self._actor_id = actor_id
        self._instructions = discord.ui.TextInput[discord.ui.Modal](
            label="Instructions",
            style=discord.TextStyle.paragraph,
            placeholder="Describe the change StudyOS should implement",
            min_length=1,
            max_length=4_000,
            required=True,
        )
        self.add_item(self._instructions)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit(
            self._mirror_id,
            self._card_message.id,
            self._actor_id,
            self._instructions.value.strip(),
            self._card_message,
            interaction,
        )
