import re

import discord

from study_discord_agent.discord_origin import DiscordOriginContext


def is_cancel_prompt(prompt: str) -> bool:
    normalized = " ".join(re.sub(r"[^a-zA-Z\s]", " ", prompt).lower().split())
    return normalized in {
        "abort",
        "cancel",
        "cancel current task",
        "cancel this",
        "nevermind",
        "never mind",
        "stop",
        "stop doing what you are doing",
        "stop doing what you are currently doing",
        "stop working",
    }


def origin_context_from_message(message: discord.Message) -> DiscordOriginContext:
    channel = message.channel
    channel_name = _str_attr(channel, "name")
    channel_type = type(channel).__name__
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        category = channel.category
        return DiscordOriginContext(
            channel_id=channel.id,
            channel_name=channel_name,
            channel_type=channel_type,
            thread_id=channel.id,
            thread_name=channel_name,
            parent_channel_id=channel.parent_id,
            parent_channel_name=_str_attr(parent, "name"),
            category_id=channel.category_id,
            category_name=_str_attr(category, "name"),
        )

    category = getattr(channel, "category", None)
    return DiscordOriginContext(
        channel_id=channel.id,
        channel_name=channel_name,
        channel_type=channel_type,
        category_id=_int_attr(channel, "category_id"),
        category_name=_str_attr(category, "name"),
    )


def _str_attr(obj: object, attr: str) -> str | None:
    value = getattr(obj, attr, None)
    return value if isinstance(value, str) and value else None


def _int_attr(obj: object, attr: str) -> int | None:
    value = getattr(obj, attr, None)
    return value if isinstance(value, int) else None
