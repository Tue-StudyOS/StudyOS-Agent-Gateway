import asyncio
import logging

import discord
from discord.ext import commands

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.discord_files import DISCORD_MESSAGE_LIMIT
from study_discord_agent.discord_markdown import discord_safe_markdown
from study_discord_agent.discord_message_context import (
    origin_context_from_message,
)
from study_discord_agent.discord_task_application import (
    DiscordTaskApplication,
    create_discord_task_application,
)
from study_discord_agent.discord_task_component_controller import (
    DiscordTaskInteractionController,
)
from study_discord_agent.github_client import GitHubClient
from study_discord_agent.github_mirror_model import GitHubMirrorEvent
from study_discord_agent.github_mirror_publisher import GitHubMirrorPublisher
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.proactive import ProactiveMonitor

logger = logging.getLogger(__name__)


class StudyBot(commands.Bot):
    discord_task_component_controller: DiscordTaskInteractionController

    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        agent: AgentGateway,
        queue: "asyncio.Queue[GitHubMirrorEvent]",
        mirror_store: GitHubMirrorStore,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = settings.discord_message_agent_enabled
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.github = github
        self.agent = agent
        self.queue = queue
        self.discord_tasks: DiscordTaskApplication = create_discord_task_application(
            self,
            settings,
            agent,
        )
        self.discord_tasks.register(self)
        self._mentions = self.discord_tasks.mentions
        self.github_mirror_publisher = GitHubMirrorPublisher(
            self,
            mirror_store,
            guild_id=settings.discord_guild_id,
            channel_id=settings.discord_pr_channel_id,
        )

    async def setup_hook(self) -> None:
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.discord_tasks.start_reconciliation(self.wait_until_ready)
        self.loop.create_task(self._notification_worker())
        if self.settings.discord_proactive_agent_enabled:
            self.loop.create_task(ProactiveMonitor(self, self.settings, self.agent).run())

    async def close(self) -> None:
        first_error: BaseException | None = None
        try:
            await self.discord_tasks.close()
        except BaseException as error:
            first_error = error
        try:
            await super().close()
        except BaseException as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error

    async def _notification_worker(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            notification = await self.queue.get()
            try:
                await self.publish_notification(notification)
            except Exception:
                logger.exception("GitHub mirror publication failed")
            finally:
                self.queue.task_done()

    async def publish_notification(self, notification: GitHubMirrorEvent) -> None:
        await self.github_mirror_publisher.publish(notification)

    async def publish_agent_message(self, message: str) -> None:
        if self.settings.discord_pr_channel_id is None:
            raise RuntimeError("DISCORD_PR_CHANNEL_ID is required for GitHub triage messages")
        channel_id = self.settings.discord_pr_channel_id
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("Configured Discord PR channel is not messageable")
        await channel.send(_discord_text(message))

    async def on_message(self, message: discord.Message) -> None:
        if not self.settings.discord_message_agent_enabled:
            return
        if message.author.bot:
            return
        if self.user is None:
            return

        mentioned = self.user in message.mentions
        prompt = (
            message.clean_content.replace(f"@{self.user.display_name}", "").strip()
            if mentioned
            else message.clean_content.strip()
        )
        if not prompt:
            if mentioned:
                await message.reply("Send a question or task after mentioning me.")
            return
        origin_context = origin_context_from_message(message)
        handled = await self._mentions.dispatch(
            message,
            prompt,
            origin_context,
            start_if_idle=mentioned,
        )
        if not handled:
            return
        logger.info(
            "discord message handled author=%s channel_id=%s message_id=%s mentioned=%s",
            message.author,
            message.channel.id,
            message.id,
            mentioned,
        )


def _discord_text(message: str) -> str:
    return discord_safe_markdown(message)[:DISCORD_MESSAGE_LIMIT]
