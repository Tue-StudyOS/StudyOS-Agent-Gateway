import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast

import discord

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings

logger = logging.getLogger(__name__)


class ProactiveMonitor:
    def __init__(self, client: discord.Client, settings: Settings, agent: AgentGateway) -> None:
        self.client = client
        self.settings = settings
        self.agent = agent
        self._last_processed_human_message_ids: dict[int, int] = {}
        self._last_sent_at_by_channel: dict[int, datetime] = {}

    async def run(self) -> None:
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await self.check_channels()
            await asyncio.sleep(self.settings.discord_proactive_interval_seconds)

    async def check_channels(self) -> None:
        channels = self.messageable_channels()
        if not channels:
            logger.warning("proactive monitor found no messageable Discord channels")
            return

        for channel in channels:
            channel_id = self._channel_id(channel)
            try:
                await self.check_channel(channel)
            except (RuntimeError, discord.DiscordException) as exc:
                logger.warning(
                    "proactive channel check failed channel_id=%s error=%s",
                    channel_id,
                    exc,
                )

    def messageable_channels(self) -> tuple[discord.abc.Messageable, ...]:
        channels: list[discord.abc.Messageable] = []
        seen: set[int] = set()

        for channel in self._candidate_channels():
            channel_id = getattr(channel, "id", None)
            if not isinstance(channel_id, int) or channel_id in seen:
                continue
            if not self._can_monitor(channel):
                continue
            seen.add(channel_id)
            channels.append(cast(discord.abc.Messageable, channel))

        return tuple(channels)

    def _candidate_channels(self) -> tuple[Any, ...]:
        channels: list[Any] = list(self.client.get_all_channels())
        for guild in self.client.guilds:
            channels.extend(getattr(guild, "threads", ()))
        return tuple(channels)

    def _can_monitor(self, channel: Any) -> bool:
        if not isinstance(channel, discord.abc.Messageable):
            return False
        if not callable(getattr(channel, "history", None)):
            return False

        guild = getattr(channel, "guild", None)
        member = getattr(guild, "me", None)
        permissions_for = getattr(channel, "permissions_for", None)
        if member is None or not callable(permissions_for):
            return True

        permissions = permissions_for(member)
        can_view = getattr(permissions, "view_channel", True)
        can_read_history = getattr(permissions, "read_message_history", True)
        can_send = getattr(permissions, "send_messages", True) or getattr(
            permissions,
            "send_messages_in_threads",
            False,
        )
        return bool(can_view and can_read_history and can_send)

    def _channel_id(self, channel: discord.abc.Messageable) -> int:
        return int(cast(Any, channel).id)

    async def check_channel(self, channel: discord.abc.Messageable) -> None:
        channel_id = self._channel_id(channel)
        history = getattr(channel, "history", None)
        if not callable(history):
            raise RuntimeError("Discord channel does not expose message history")

        messages = [message async for message in cast(Any, channel).history(limit=20)]
        latest_human_message = self.latest_recent_human_message(messages)
        if latest_human_message is None:
            logger.info("proactive stale-or-empty channel_id=%s", channel_id)
            return
        latest_human_message_id = int(latest_human_message.id)
        if self._last_processed_human_message_ids.get(channel_id) == latest_human_message_id:
            logger.info("proactive no-new-human-message channel_id=%s", channel_id)
            return
        if self._is_in_post_cooldown(channel_id):
            logger.info("proactive cooldown channel_id=%s", channel_id)
            return
        self._last_processed_human_message_ids[channel_id] = latest_human_message_id

        context = "\n".join(
            f"{message.author}: {message.clean_content}" for message in reversed(messages)
        )
        prompt = (
            "Review this recent Discord channel history as the StudyOS course coding "
            "partner. Prefer NO_ACTION unless one concise message would clearly help: "
            "unblock the group, add concrete technical/product context, identify a "
            "security/privacy/cost risk, connect the discussion to reusable StudyOS/Tue "
            "API wrapper capabilities, or suggest a next step. Do not spam, do not send "
            "multiple follow-ups in a row, and do not create issues or PRs from a "
            "proactive check; instead ask whether the group wants an issue/spec or "
            "implementation when the discussion looks ready. If no response is useful, "
            f"answer exactly NO_ACTION.\n\nRecent messages:\n{context}"
        )
        reply = await self.agent.ask(
            prompt,
            user="discord-proactive-monitor",
            channel_id=channel_id,
        )
        text = reply.message.strip()
        if not text or text == "NO_ACTION":
            logger.info("proactive no-action channel_id=%s", channel_id)
            return
        if self.settings.discord_proactive_dry_run:
            logger.info("proactive dry-run channel_id=%s message=%s", channel_id, text[:500])
            return
        sent_message = await channel.send(text[:1900])
        self._last_sent_at_by_channel[channel_id] = datetime.now(UTC)
        logger.info(
            "proactive sent channel_id=%s message_id=%s",
            channel_id,
            getattr(sent_message, "id", None),
        )

    def _is_in_post_cooldown(self, channel_id: int) -> bool:
        last_sent_at = self._last_sent_at_by_channel.get(channel_id)
        if last_sent_at is None:
            return False
        elapsed = (datetime.now(UTC) - last_sent_at).total_seconds()
        return elapsed < self.settings.discord_proactive_min_post_interval_seconds

    def latest_recent_human_message(self, messages: list[Any]) -> Any | None:
        human_messages = [
            message
            for message in messages
            if not getattr(getattr(message, "author", None), "bot", False)
        ]
        if not human_messages:
            return None

        latest = max(human_messages, key=lambda message: message.created_at)
        age_seconds = (datetime.now(UTC) - latest.created_at).total_seconds()
        if age_seconds > self.settings.discord_proactive_recent_activity_seconds:
            return None
        return latest
