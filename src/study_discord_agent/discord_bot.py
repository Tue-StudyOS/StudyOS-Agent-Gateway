import asyncio
import logging
from collections import deque
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
from study_discord_agent.discord_markdown import discord_safe_markdown
from study_discord_agent.discord_message_context import (
    is_cancel_prompt,
    origin_context_from_message,
)
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.github_client import GitHubClient
from study_discord_agent.github_events import DiscordNotification
from study_discord_agent.proactive import ProactiveMonitor

logger = logging.getLogger(__name__)
MAX_SEEN_MESSAGE_IDS = 2048


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
        self._active_mention_tasks: dict[int, asyncio.Task[None]] = {}
        self._mention_generations: dict[int, int] = {}
        self._mention_lock = asyncio.Lock()
        self._seen_message_ids: set[int] = set()
        self._seen_message_order: deque[int] = deque()

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
                await channel.send(
                    _discord_text(notification.followup_message),
                )
        if self.settings.agent_auto_review_enabled and notification.agent_prompt:
            try:
                reply = await self.agent.ask(
                    prompt=notification.agent_prompt,
                    user="github-webhook",
                    channel_id=channel_id,
                )
                if channel is not None:
                    await channel.send(_discord_text(reply.message))
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
        await channel.send(_discord_text(message))

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

        origin_context = origin_context_from_message(message)
        await self._dispatch_agent_mention(message, prompt, origin_context)

    async def _dispatch_agent_mention(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> None:
        channel_id = message.channel.id
        previous_task: asyncio.Task[None] | None = None
        cancel_only = is_cancel_prompt(prompt)

        async with self._mention_lock:
            if message.id in self._seen_message_ids:
                logger.info("duplicate discord mention ignored message_id=%s", message.id)
                return
            self._remember_seen_message_id(message.id)
            generation = self._mention_generations.get(channel_id, 0) + 1
            self._mention_generations[channel_id] = generation

            previous_task = self._active_mention_tasks.get(channel_id)
            if previous_task and not previous_task.done():
                previous_task.cancel()
            elif previous_task and previous_task.done():
                previous_task = None

        if previous_task:
            await _await_cancelled_task(previous_task)

        if cancel_only:
            message_text = (
                "Stopped the active task in this channel."
                if previous_task
                else "No active task is running in this channel."
            )
            await message.reply(message_text)
            return

        async with self._mention_lock:
            if self._mention_generations.get(channel_id) != generation:
                logger.info(
                    "discord mention superseded before start channel_id=%s message_id=%s",
                    channel_id,
                    message.id,
                )
                return
            task = asyncio.create_task(
                self._handle_agent_mention(message, prompt, origin_context)
            )
            self._active_mention_tasks[channel_id] = task
            task.add_done_callback(
                lambda done: asyncio.create_task(
                    self._forget_mention_task(channel_id, done)
                )
            )

    def _remember_seen_message_id(self, message_id: int) -> None:
        self._seen_message_ids.add(message_id)
        self._seen_message_order.append(message_id)
        while len(self._seen_message_order) > MAX_SEEN_MESSAGE_IDS:
            expired = self._seen_message_order.popleft()
            self._seen_message_ids.discard(expired)

    async def _forget_mention_task(
        self,
        channel_id: int,
        task: asyncio.Task[None],
    ) -> None:
        async with self._mention_lock:
            if self._active_mention_tasks.get(channel_id) is task:
                self._active_mention_tasks.pop(channel_id, None)

    def has_active_mention_task(self, channel_id: int) -> bool:
        task = self._active_mention_tasks.get(channel_id)
        return task is not None and not task.done()

    async def _handle_agent_mention(
        self,
        message: discord.Message,
        prompt: str,
        origin_context: DiscordOriginContext,
    ) -> None:
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
                    origin_context=origin_context,
                )
                await self._reply_to_message(message, reply)
                logger.info("discord mention replied message_id=%s", message.id)
            except (RuntimeError, discord.HTTPException) as exc:
                await message.reply(f"Agent failed: {exc}")
                logger.warning("discord mention failed message_id=%s error=%s", message.id, exc)

    async def _reply_to_message(self, message: discord.Message, reply: AgentReply) -> None:
        if not reply.files:
            await message.reply(_discord_text(reply.message))
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
                content=_discord_text(reply.message) or None,
                files=files,
            )
        finally:
            for file in files:
                file.close()


async def _await_cancelled_task(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("cancelled discord mention task ended with error: %s", exc)


def _discord_text(message: str) -> str:
    return discord_safe_markdown(message)[:DISCORD_MESSAGE_LIMIT]
