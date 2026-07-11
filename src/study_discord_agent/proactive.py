import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, cast

import discord

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.discord_markdown import discord_safe_markdown

logger = logging.getLogger(__name__)
MIN_HUMAN_RESPONSE_WINDOW_SECONDS = 120
MAX_PROACTIVE_MESSAGE_CHARS = 500
MAX_PROACTIVE_MESSAGE_LINES = 4
FAILURE_SIGNAL_RE = re.compile(
    r"(?:\bblocked\b|\bblocker\b|\bstuck\b|\berror\b|\bexception\b|\btraceback\b|"
    r"\bbug\b|\bbroken\b|\bfail(?:ed|ing)?\b|\bcrash(?:ed|ing)?\b|\btimeout\b|"
    r"\bdoesn['’]?t work\b|\bcan['’]?t (?:build|run|connect|authenticate)\b)",
    re.IGNORECASE,
)
TECHNICAL_CONTEXT_RE = re.compile(
    r"\b(?:api|auth|token|code|repo|git|github|branch|build|test|ci|deploy|server|"
    r"client|database|db|parser|compiler|package|dependency|model|agent|discord|"
    r"endpoint|request|response)\b",
    re.IGNORECASE,
)
PROACTIVE_MARKDOWN_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s|[-*>]\s|\d+[.)]\s)")


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
        if not is_private_group_space(channel):
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
            "Decide whether one tiny proactive reply would unblock this StudyOS group. "
            "Silence is the default. Post only when the latest human message contains an "
            "unanswered technical blocker and you can add a concrete fix, diagnostic, or "
            "missing fact that the students have not already said. Do not summarize the "
            "conversation, cheerlead, restate a question, offer generic next steps, or ask "
            "whether they want an issue/PR. Do not post code, Markdown sections, lists, or "
            "more than two short sentences. Sound like a friendly fellow student, not a "
            'support bot. Return only JSON: {"action":"NO_ACTION"} or '
            '{"action":"POST","message":"..."}.\n\n'
            f"Recent messages:\n{context}"
        )
        reply = await self.agent.ask(
            prompt,
            user="discord-proactive-monitor",
            channel_id=channel_id,
        )
        text = proactive_post_text(reply.message)
        if text is None or reply.files:
            logger.info("proactive no-action channel_id=%s", channel_id)
            return
        if not await self._still_actionable(channel, latest_human_message_id):
            logger.info("proactive became-stale channel_id=%s", channel_id)
            return
        if self.settings.discord_proactive_dry_run:
            logger.info("proactive dry-run channel_id=%s message=%s", channel_id, text[:500])
            return
        sent_message = await channel.send(
            text,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._last_sent_at_by_channel[channel_id] = datetime.now(UTC)
        logger.info(
            "proactive sent channel_id=%s message_id=%s",
            channel_id,
            getattr(sent_message, "id", None),
        )

    async def _still_actionable(
        self,
        channel: discord.abc.Messageable,
        expected_message_id: int,
    ) -> bool:
        if not is_private_group_space(channel):
            return False
        history = getattr(channel, "history", None)
        if not callable(history):
            return False
        messages = [message async for message in cast(Any, channel).history(limit=20)]
        latest = self.latest_recent_human_message(messages)
        return latest is not None and int(latest.id) == expected_message_id

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
        if not (
            MIN_HUMAN_RESPONSE_WINDOW_SECONDS
            <= age_seconds
            <= self.settings.discord_proactive_recent_activity_seconds
        ):
            return None
        if not is_high_signal_message(str(getattr(latest, "clean_content", ""))):
            return None
        if self.client.user in getattr(latest, "mentions", ()):
            return None
        for message in messages:
            author = getattr(message, "author", None)
            if not getattr(author, "bot", False):
                continue
            bot_age = (datetime.now(UTC) - message.created_at).total_seconds()
            if bot_age <= self.settings.discord_proactive_min_post_interval_seconds:
                return None
        return latest


def is_high_signal_message(text: str) -> bool:
    return bool(
        FAILURE_SIGNAL_RE.search(text) or ("?" in text and TECHNICAL_CONTEXT_RE.search(text))
    )


def is_group_space(channel: Any) -> bool:
    names = (
        str(getattr(channel, "name", "")).lower(),
        str(getattr(getattr(channel, "parent", None), "name", "")).lower(),
    )
    return any(name.startswith("group-") for name in names)


def is_private_group_space(channel: Any) -> bool:
    if not is_group_space(channel):
        return False
    guild = getattr(channel, "guild", None)
    default_role = getattr(guild, "default_role", None)
    permissions_for = getattr(channel, "permissions_for", None)
    if default_role is None or not callable(permissions_for):
        return False
    return not bool(getattr(permissions_for(default_role), "view_channel", True))


def proactive_post_text(response: str) -> str | None:
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    payload = cast(dict[str, object], data)
    if payload.get("action") != "POST":
        return None
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    text = discord_safe_markdown(message).strip()
    if (
        not text
        or len(text) > MAX_PROACTIVE_MESSAGE_CHARS
        or len(text.splitlines()) > MAX_PROACTIVE_MESSAGE_LINES
        or "```" in text
        or "~~~" in text
        or PROACTIVE_MARKDOWN_RE.search(text)
    ):
        return None
    return text
