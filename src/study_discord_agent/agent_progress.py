from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class AgentProgress:
    now: str | None = None
    completed: str | None = None
    next_step: str | None = None


def progress_from_notification(method: str, params: dict[str, object]) -> AgentProgress | None:
    if method == "turn/started":
        return AgentProgress(now="Understanding the request and planning the work")
    if method not in {"item/started", "item/completed"}:
        return None

    item_obj = params.get("item")
    if not isinstance(item_obj, dict):
        return None
    item = cast(dict[str, Any], item_obj)
    item_type = item.get("type")
    if not isinstance(item_type, str):
        return None

    if method == "item/started":
        return _started_progress(item_type, item)
    return _completed_progress(item_type, item)


def _started_progress(item_type: str, item: dict[str, Any]) -> AgentProgress | None:
    messages = {
        "commandExecution": "Running a repository command",
        "fileChange": "Applying focused file changes",
        "webSearch": "Searching the web",
        "imageView": "Inspecting an image",
        "imageGeneration": "Generating an image",
        "collabAgentToolCall": "Coordinating parallel work",
        "subAgentActivity": "Coordinating parallel work",
    }
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        return AgentProgress(now="Using an integration tool")
    message = messages.get(item_type)
    return AgentProgress(now=message) if message else None


def _completed_progress(item_type: str, item: dict[str, Any]) -> AgentProgress | None:
    if item_type == "agentMessage" and item.get("phase") == "commentary":
        return AgentProgress(now="Reviewing progress and continuing the work")
    if item_type == "plan":
        return AgentProgress(
            now="Following the updated plan",
            completed="Updated the implementation plan",
            next_step="Continue with the next planned step",
        )
    if item_type == "commandExecution":
        failed = item.get("status") in {"failed", "declined"}
        return AgentProgress(
            now="Reviewing command results",
            completed="A repository command failed" if failed else "Repository command completed",
        )
    if item_type == "fileChange":
        changes = item.get("changes")
        count = len(cast(list[object], changes)) if isinstance(changes, list) else 0
        label = f"Updated {count} file{'s' if count != 1 else ''}" if count else "Updated files"
        return AgentProgress(now="Continuing the implementation", completed=label)
    if item_type in {"mcpToolCall", "dynamicToolCall"}:
        failed = item.get("status") == "failed" or item.get("success") is False
        return AgentProgress(
            now="Reviewing integration results",
            completed="Integration call failed" if failed else "Integration call completed",
        )
    if item_type == "webSearch":
        return AgentProgress(now="Reviewing research results", completed="Web research completed")
    if item_type in {"collabAgentToolCall", "subAgentActivity"}:
        return AgentProgress(now="Integrating delegated work", completed="Delegated work updated")
    return None
