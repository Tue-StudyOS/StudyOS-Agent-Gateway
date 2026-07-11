from pathlib import Path
from typing import Any, cast

import httpx

from study_discord_agent.artifacts import parse_agent_reply, parse_artifact_files
from study_discord_agent.discord_origin import DiscordOriginContext


async def request_agent_webhook(
    webhook_url: str,
    *,
    prompt: str,
    user: str,
    channel_id: int | None,
    source_message_id: int | None,
    attachment_paths: tuple[Path, ...],
    origin_context: DiscordOriginContext | None,
) -> tuple[str, tuple[Path, ...]]:
    payload: dict[str, object] = {
        "prompt": prompt,
        "source": "discord",
        "user": user,
        "channel_id": channel_id,
        "source_message_id": source_message_id,
        "attachments": [str(path) for path in attachment_paths],
    }
    if origin_context:
        payload["origin_context"] = _origin_payload(origin_context)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()
        data = cast(dict[str, Any], response.json())

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise RuntimeError("Agent response must contain a non-empty message")
    parsed = parse_agent_reply(message)
    return parsed.message, parsed.files + parse_artifact_files(data.get("files"))


def _origin_payload(origin: DiscordOriginContext) -> dict[str, object]:
    return {
        "channel_id": origin.channel_id,
        "channel_name": origin.channel_name,
        "channel_type": origin.channel_type,
        "thread_id": origin.thread_id,
        "thread_name": origin.thread_name,
        "parent_channel_id": origin.parent_channel_id,
        "parent_channel_name": origin.parent_channel_name,
        "category_id": origin.category_id,
        "category_name": origin.category_name,
    }
