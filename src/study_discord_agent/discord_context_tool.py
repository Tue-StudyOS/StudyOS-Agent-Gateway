import argparse
import asyncio
import os
from typing import Any, NoReturn, cast

import httpx

from study_discord_agent.discord_origin import DiscordOriginContext, render_origin_context

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_THREAD_TYPES = {10, 11, 12}


def main() -> None:
    args = _parse_args()
    try:
        output = asyncio.run(
            fetch_context(
                channel_id=args.channel_id,
                limit=args.limit,
                before_message_id=args.before_message_id,
                around_message_id=args.around_message_id,
                token=os.environ.get("DISCORD_TOKEN"),
            )
        )
    except RuntimeError as exc:
        _fail(str(exc))
    print(output)


async def fetch_context(
    channel_id: int,
    limit: int,
    before_message_id: int | None,
    around_message_id: int | None,
    token: str | None,
) -> str:
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required to fetch Discord context")
    if before_message_id and around_message_id:
        raise RuntimeError("Use only one of --before-message-id or --around-message-id")

    params: dict[str, int] = {"limit": limit}
    if before_message_id:
        params["before"] = before_message_id
    if around_message_id:
        params["around"] = around_message_id

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        channel_data = await _fetch_channel(client, channel_id, headers)
        origin_context = await _fetch_origin_context(client, channel_data, headers)
        response = await client.get(url, headers=headers, params=params)

    if response.status_code == 403:
        raise RuntimeError("Discord bot lacks permission to read that channel")
    if response.status_code == 404:
        raise RuntimeError("Discord channel or message was not found")
    response.raise_for_status()

    messages = cast(list[dict[str, Any]], response.json())
    messages.sort(key=lambda item: int(str(item.get("id", "0"))))
    return render_messages(channel_id, messages, origin_context)


def render_messages(
    channel_id: int,
    messages: list[dict[str, Any]],
    origin_context: DiscordOriginContext | None = None,
) -> str:
    lines = [f"Discord context for channel {channel_id}:"]
    rendered_origin = render_origin_context(origin_context)
    if rendered_origin:
        lines.extend(rendered_origin.splitlines())
    if not messages:
        lines.append("- No messages returned.")
        return "\n".join(lines)

    for message in messages:
        author = cast(dict[str, Any], message.get("author") or {})
        username = str(author.get("global_name") or author.get("username") or "unknown")
        bot_marker = " bot" if author.get("bot") else ""
        timestamp = str(message.get("timestamp") or "unknown-time")
        content = _message_content(message)
        lines.append(f"- [{timestamp}] {username}{bot_marker}: {content}")
    return "\n".join(lines)


async def _fetch_channel(
    client: httpx.AsyncClient,
    channel_id: int,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = await client.get(f"{DISCORD_API_BASE}/channels/{channel_id}", headers=headers)
    if response.status_code == 403:
        raise RuntimeError("Discord bot lacks permission to read that channel")
    if response.status_code == 404:
        raise RuntimeError("Discord channel or message was not found")
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


async def _fetch_origin_context(
    client: httpx.AsyncClient,
    channel: dict[str, Any],
    headers: dict[str, str],
) -> DiscordOriginContext:
    channel_id = _int_or_none(channel.get("id")) or 0
    channel_type = _int_or_none(channel.get("type"))
    channel_name = _str_or_none(channel.get("name"))
    if channel_type in DISCORD_THREAD_TYPES:
        parent_id = _int_or_none(channel.get("parent_id"))
        parent = await _fetch_optional_channel(client, parent_id, headers)
        category_id = _int_or_none(parent.get("parent_id")) if parent else None
        category = await _fetch_optional_channel(client, category_id, headers)
        return DiscordOriginContext(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_type=_channel_type_name(channel_type),
            thread_id=channel_id,
            thread_name=channel_name,
            parent_channel_id=parent_id,
            parent_channel_name=_str_or_none(parent.get("name")) if parent else None,
            category_id=category_id,
            category_name=_str_or_none(category.get("name")) if category else None,
        )

    category_id = _int_or_none(channel.get("parent_id"))
    category = await _fetch_optional_channel(client, category_id, headers)
    return DiscordOriginContext(
        channel_id=channel_id,
        channel_name=channel_name,
        channel_type=_channel_type_name(channel_type),
        category_id=category_id,
        category_name=_str_or_none(category.get("name")) if category else None,
    )


async def _fetch_optional_channel(
    client: httpx.AsyncClient,
    channel_id: int | None,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    if channel_id is None:
        return None
    response = await client.get(f"{DISCORD_API_BASE}/channels/{channel_id}", headers=headers)
    if response.status_code in {403, 404}:
        return None
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


def _message_content(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "").strip()
    if not content and message.get("attachments"):
        content = "[attachment]"
    if not content and message.get("embeds"):
        content = "[embed]"
    content = " ".join(content.split())
    if len(content) <= 700:
        return content
    return content[:697].rstrip() + "..."


def _channel_type_name(channel_type: int | None) -> str | None:
    if channel_type is None:
        return None
    names = {
        0: "text",
        2: "voice",
        4: "category",
        5: "announcement",
        10: "announcement_thread",
        11: "public_thread",
        12: "private_thread",
        15: "forum",
    }
    return names.get(channel_type)


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch recent Discord channel messages for StudyOS agent context."
    )
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--before-message-id", type=int)
    parser.add_argument("--around-message-id", type=int)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")
    return args


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"studyos-discord-context failed: {message}")
