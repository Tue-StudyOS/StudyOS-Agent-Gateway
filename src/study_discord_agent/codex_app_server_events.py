from collections.abc import Mapping
from typing import cast

from study_discord_agent.codex_app_server_protocol import AppServerRpcError
from study_discord_agent.codex_command import AgentUsage


def agent_message(params: Mapping[str, object]) -> tuple[str | None, str] | None:
    item_obj = params.get("item")
    if not isinstance(item_obj, dict):
        return None
    item = cast(dict[str, object], item_obj)
    if item.get("type") != "agentMessage":
        return None
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    phase = item.get("phase")
    return phase if isinstance(phase, str) else None, text


def usage_from_notification(params: Mapping[str, object]) -> AgentUsage:
    token_usage_obj = params.get("tokenUsage")
    if not isinstance(token_usage_obj, dict):
        return AgentUsage()
    token_usage = cast(dict[str, object], token_usage_obj)
    last_obj = token_usage.get("last")
    if not isinstance(last_obj, dict):
        return AgentUsage()
    last = cast(dict[str, object], last_obj)
    return AgentUsage(
        input_tokens=_positive_int(last.get("inputTokens")),
        cached_input_tokens=_positive_int(last.get("cachedInputTokens")),
        output_tokens=_positive_int(last.get("outputTokens")),
        reasoning_output_tokens=_positive_int(last.get("reasoningOutputTokens")),
    )


def turn_error_message(error: object) -> str:
    if isinstance(error, dict):
        message = cast(dict[str, object], error).get("message")
        if isinstance(message, str):
            return message
    return "Codex app-server turn failed"


def is_not_steerable_error(error: AppServerRpcError) -> bool:
    message = str(error).lower()
    return "steer" in message or "active turn" in message or "expectedturnid" in message


def notification_turn_id(params: Mapping[str, object]) -> object:
    turn_id = params.get("turnId")
    turn_obj = params.get("turn")
    if turn_id is None and isinstance(turn_obj, dict):
        return cast(dict[str, object], turn_obj).get("id")
    return turn_id


def _positive_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
