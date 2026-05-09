import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.github_client import GitHubClient, GitHubRef
from study_discord_agent.github_events import DiscordNotification


class StudyBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        agent: AgentGateway,
        queue: "asyncio.Queue[DiscordNotification]",
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = settings.discord_message_agent_enabled
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.github = github
        self.agent = agent
        self.queue = queue

    async def setup_hook(self) -> None:
        self.tree.add_command(study_group(self))
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.loop.create_task(self._notification_worker())

    async def _notification_worker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            notification = await self.queue.get()
            try:
                await self.publish_notification(notification)
            finally:
                self.queue.task_done()

    async def publish_notification(self, notification: DiscordNotification) -> None:
        channel = self.get_channel(self.settings.discord_pr_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.settings.discord_pr_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("Configured Discord PR channel is not messageable")

        embed = discord.Embed(
            title=notification.title,
            url=notification.url,
            description=notification.description,
            color=notification.color,
        )
        await channel.send(embed=embed)
        if self.settings.agent_auto_review_enabled and notification.agent_prompt:
            try:
                reply = await self.agent.ask(
                    prompt=notification.agent_prompt,
                    user="github-webhook",
                    channel_id=self.settings.discord_pr_channel_id,
                )
                await channel.send(reply.message[:1900])
            except RuntimeError as exc:
                await channel.send(f"Agent review failed: {exc}")

    async def publish_agent_message(self, message: str) -> None:
        channel = self.get_channel(self.settings.discord_pr_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.settings.discord_pr_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("Configured Discord PR channel is not messageable")
        await channel.send(message[:1900])

    async def on_message(self, message: discord.Message) -> None:
        if not self.settings.discord_message_agent_enabled:
            return
        if message.author.bot:
            return
        if self.user is None or self.user not in message.mentions:
            return

        prompt = message.clean_content.replace(f"@{self.user.display_name}", "").strip()
        if not prompt:
            await message.reply("Send a question or task after mentioning me.")
            return

        async with message.channel.typing():
            try:
                reply = await self.agent.ask(
                    prompt=prompt,
                    user=str(message.author),
                    channel_id=message.channel.id,
                )
                await message.reply(reply.message[:1900])
            except RuntimeError as exc:
                await message.reply(f"Agent failed: {exc}")


def study_group(bot: StudyBot) -> app_commands.Group:
    group = app_commands.Group(name="study", description="Study project collaboration commands")

    @group.command(name="ping", description="Check whether the bot is alive")
    async def ping(  # pyright: ignore[reportUnusedFunction]
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.send_message("pong", ephemeral=True)

    @group.command(name="pr-comment", description="Comment on a pull request")
    @app_commands.describe(number="Pull request number", body="Comment body", repo="owner/name")
    async def pr_comment(  # pyright: ignore[reportUnusedFunction]
        interaction: discord.Interaction,
        number: int,
        body: str,
        repo: str | None = None,
    ) -> None:
        await _require_allowed(interaction, bot.settings)
        target = _repo_from_input(repo, bot.settings)
        url = await bot.github.comment_on_issue(target, number, body)
        await interaction.response.send_message(f"Comment posted: {url}", ephemeral=True)

    @group.command(name="pr-merge", description="Squash merge a pull request")
    @app_commands.describe(number="Pull request number", repo="owner/name")
    async def pr_merge(  # pyright: ignore[reportUnusedFunction]
        interaction: discord.Interaction,
        number: int,
        repo: str | None = None,
    ) -> None:
        await _require_allowed(interaction, bot.settings)
        target = _repo_from_input(repo, bot.settings)
        result = await bot.github.merge_pull_request(target, number)
        await interaction.response.send_message(f"PR merged: {result}", ephemeral=True)

    @group.command(name="agent", description="Ask the configured course agent")
    @app_commands.describe(prompt="Question or task for the agent")
    async def ask_agent(  # pyright: ignore[reportUnusedFunction]
        interaction: discord.Interaction,
        prompt: str,
    ) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)
        reply = await bot.agent.ask(
            prompt=prompt,
            user=str(interaction.user),
            channel_id=interaction.channel_id or 0,
        )
        await interaction.followup.send(reply.message[:1900])

    return group


def _repo_from_input(repo: str | None, settings: Settings) -> GitHubRef:
    value = repo or settings.github_repository
    if not value:
        raise app_commands.AppCommandError("Repository is required as owner/name")
    return GitHubRef.parse(value)


async def _require_allowed(interaction: discord.Interaction, settings: Settings) -> None:
    if not settings.github_write_enabled:
        raise app_commands.AppCommandError("GitHub write actions are disabled")
    if not settings.allowed_role_ids:
        return

    user_roles = getattr(interaction.user, "roles", [])
    role_ids = {role.id for role in user_roles}
    if settings.allowed_role_ids.isdisjoint(role_ids):
        raise app_commands.AppCommandError("You are not allowed to run this command")
