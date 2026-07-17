import re
from typing import Any, Protocol, Self, cast

import discord

from study_discord_agent.github_mirror_model import GitHubMirrorAction

GITHUB_ACTION_TEMPLATE = (
    r"^studyos:github:(?P<action>review|security_review|vulnerability_scan|work):"
    r"(?P<mirror_id>[0-9a-f]{32})$"
)


class GitHubMirrorComponentController(Protocol):
    async def handle_mirror_action(
        self,
        action: GitHubMirrorAction,
        mirror_id: str,
        interaction: discord.Interaction,
    ) -> None: ...


class GitHubMirrorActionItem(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.LayoutView]],
    template=GITHUB_ACTION_TEMPLATE,
):
    def __init__(
        self,
        item: discord.ui.Button[discord.ui.LayoutView],
        action: GitHubMirrorAction,
        mirror_id: str,
    ) -> None:
        super().__init__(item)
        self.action = action
        self.mirror_id = mirror_id

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
            raise TypeError("StudyOS GitHub actions must be Discord buttons")
        return cls(
            cast(discord.ui.Button[discord.ui.LayoutView], item),
            GitHubMirrorAction(match.group("action")),
            match.group("mirror_id"),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        controller = getattr(interaction.client, "github_mirror_controller", None)
        handler = getattr(controller, "handle_mirror_action", None)
        if not callable(handler):
            await interaction.response.send_message(
                "GitHub task actions are temporarily unavailable.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        typed = cast(GitHubMirrorComponentController, controller)
        await typed.handle_mirror_action(self.action, self.mirror_id, interaction)
