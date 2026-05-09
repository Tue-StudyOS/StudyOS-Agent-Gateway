from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class DiscordNotification:
    title: str
    url: str
    description: str
    color: int
    agent_prompt: str | None = None


def notification_from_github_event(
    event_name: str,
    payload: dict[str, Any],
) -> DiscordNotification | None:
    if event_name == "pull_request":
        return _pull_request_notification(payload)
    if event_name == "issues":
        return _issue_notification(payload)
    return None


def _pull_request_notification(payload: dict[str, Any]) -> DiscordNotification | None:
    action = str(payload.get("action", ""))
    if action not in {"opened", "reopened", "ready_for_review", "closed", "synchronize"}:
        return None

    pull_request = _object(payload, "pull_request")
    repository = _object(payload, "repository")
    sender = _object(payload, "sender")

    number = int(pull_request["number"])
    title = str(pull_request["title"])
    url = str(pull_request["html_url"])
    repo_name = str(repository["full_name"])
    author = str(sender["login"])
    state = "merged" if pull_request.get("merged") else action.replace("_", " ")

    agent_prompt = None
    if action in {"opened", "ready_for_review", "synchronize"}:
        agent_prompt = (
            f"Review pull request #{number} in {repo_name}: {url}\n"
            "Post a concise Discord summary with risks, likely review focus, and next steps. "
            "Do not merge or close anything unless explicitly instructed."
        )

    return DiscordNotification(
        title=f"PR #{number} {state}: {title}",
        url=url,
        description=f"{repo_name} by @{author}",
        color=_color_for_action(action, bool(pull_request.get("merged"))),
        agent_prompt=agent_prompt,
    )


def _issue_notification(payload: dict[str, Any]) -> DiscordNotification | None:
    action = str(payload.get("action", ""))
    if action not in {"opened", "reopened", "closed"}:
        return None

    issue = _object(payload, "issue")
    repository = _object(payload, "repository")
    sender = _object(payload, "sender")

    number = int(issue["number"])
    title = str(issue["title"])
    url = str(issue["html_url"])
    repo_name = str(repository["full_name"])
    author = str(sender["login"])

    return DiscordNotification(
        title=f"Issue #{number} {action}: {title}",
        url=url,
        description=f"{repo_name} by @{author}",
        color=_color_for_action(action, merged=False),
    )


def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"GitHub payload missing object: {key}")
    return cast(dict[str, Any], value)


def _color_for_action(action: str, merged: bool) -> int:
    if merged:
        return 0x8250DF
    if action in {"opened", "reopened", "ready_for_review"}:
        return 0x2DA44E
    if action == "closed":
        return 0xCF222E
    return 0x0969DA
