import asyncio

import discord
from discord.ext import commands

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.github_client import GitHubClient
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
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            self.tree.clear_commands(guild=None)
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
        if notification.followup_message:
            await channel.send(notification.followup_message[:1900])
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
