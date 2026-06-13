import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import commands

from study_discord_agent.agent import AgentGateway, AgentReply
from study_discord_agent.config import Settings
from study_discord_agent.discord_files import (
    DISCORD_MESSAGE_LIMIT,
    save_message_attachments,
    validate_artifact_files,
)
from study_discord_agent.github_client import GitHubClient
from study_discord_agent.github_events import DiscordNotification
from study_discord_agent.proactive import ProactiveMonitor

logger = logging.getLogger(__name__)


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
        if self.settings.discord_proactive_agent_enabled:
            self.loop.create_task(ProactiveMonitor(self, self.settings, self.agent).run())

    async def _notification_worker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            notification = await self.queue.get()
            try:
                await self.publish_notification(notification)
            finally:
                self.queue.task_done()

    async def publish_notification(self, notification: DiscordNotification) -> None:
        channel_id = self.settings.discord_pr_channel_id
        channel: discord.abc.Messageable | None = None
        if channel_id is not None:
            resolved_channel = self.get_channel(channel_id)
            if resolved_channel is None:
                resolved_channel = await self.fetch_channel(channel_id)
            if not isinstance(resolved_channel, discord.abc.Messageable):
                raise RuntimeError("Configured Discord PR channel is not messageable")
            channel = resolved_channel

            embed = discord.Embed(
                title=notification.title,
                url=notification.url,
                description=notification.description,
                color=notification.color,
            )
            await channel.send(embed=embed)
            if notification.followup_message:
                await channel.send(notification.followup_message[:DISCORD_MESSAGE_LIMIT])
        if self.settings.agent_auto_review_enabled and notification.agent_prompt:
            try:
                reply = await self.agent.ask(
                    prompt=notification.agent_prompt,
                    user="github-webhook",
                    channel_id=channel_id,
                )
                if channel is not None:
                    await channel.send(reply.message[:DISCORD_MESSAGE_LIMIT])
            except RuntimeError as exc:
                if channel is not None:
                    await channel.send(f"Agent review failed: {exc}")
                else:
                    logger.warning("GitHub webhook agent run failed: %s", exc)
        elif channel is None:
            logger.info(
                "GitHub webhook notification ignored because no Discord channel is configured"
            )

    async def publish_agent_message(self, message: str) -> None:
        if self.settings.discord_pr_channel_id is None:
            raise RuntimeError("DISCORD_PR_CHANNEL_ID is required for GitHub triage messages")
        channel_id = self.settings.discord_pr_channel_id
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("Configured Discord PR channel is not messageable")
        await channel.send(message[:DISCORD_MESSAGE_LIMIT])

    async def on_message(self, message: discord.Message) -> None:
        if not self.settings.discord_message_agent_enabled:
            return
        if message.author.bot:
            return
        if self.user is None or self.user not in message.mentions:
            return

        prompt = message.clean_content.replace(f"@{self.user.display_name}", "").strip()
        logger.info(
            "discord mention received author=%s channel_id=%s message_id=%s",
            message.author,
            message.channel.id,
            message.id,
        )
        if not prompt:
            await message.reply("Send a question or task after mentioning me.")
            return

        async with message.channel.typing():
            try:
                attachments = await save_message_attachments(
                    message,
                    Path(self.settings.discord_attachment_dir),
                )
                reply = await self.agent.ask(
                    prompt=prompt,
                    user=str(message.author),
                    channel_id=message.channel.id,
                    source_message_id=message.id,
                    attachment_paths=attachments,
                )
                await self._reply_to_message(message, reply)
                logger.info("discord mention replied message_id=%s", message.id)
            except (RuntimeError, discord.HTTPException) as exc:
                await message.reply(f"Agent failed: {exc}")
                logger.warning("discord mention failed message_id=%s error=%s", message.id, exc)

    async def _reply_to_message(self, message: discord.Message, reply: AgentReply) -> None:
        if not reply.files:
            await message.reply(reply.message[:DISCORD_MESSAGE_LIMIT])
            return

        roots = tuple(Path(root) for root in self.settings.discord_artifact_allowed_root_list)
        paths = validate_artifact_files(
            reply.files,
            roots,
            self.settings.discord_artifact_max_bytes,
        )
        files = [discord.File(path) for path in paths]
        try:
            await message.reply(
                content=reply.message[:DISCORD_MESSAGE_LIMIT] or None,
                files=files,
            )
        finally:
            for file in files:
                file.close()
