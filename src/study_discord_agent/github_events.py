from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class DiscordNotification:
    title: str
    url: str
    description: str
    color: int
    followup_message: str | None = None
    agent_prompt: str | None = None


def notification_from_github_event(
    event_name: str,
    payload: dict[str, Any],
) -> DiscordNotification | None:
    if event_name == "pull_request":
        return _pull_request_notification(payload)
    if event_name == "issues":
        return _issue_notification(payload)
    if event_name == "issue_comment":
        return _issue_comment_notification(payload)
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
    followup_message = None
    if action in {"opened", "ready_for_review", "synchronize"}:
        followup_message = (
            f"Review invitation for PR #{number}: pick one small review angle if you have "
            "10 minutes, ask a question if something is unclear, or leave a note about what "
            "would make this easier to review. Humans own the final merge."
        )
        agent_prompt = (
            f"Review pull request #{number} in {repo_name}: {url}\n"
            "Post a concise Discord summary that lowers the threshold for student review: "
            "summarize the intent, likely risk areas, useful review angles, and concrete next "
            "steps. Never merge the PR. Merging is reserved for StudyOS students through GitHub."
        )

    return DiscordNotification(
        title=f"PR #{number} {state}: {title}",
        url=url,
        description=f"{repo_name} by @{author}",
        color=_color_for_action(action, bool(pull_request.get("merged"))),
        followup_message=followup_message,
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

    agent_prompt = None
    if action in {"opened", "reopened"}:
        agent_prompt = _issue_refinement_prompt(repo_name, number, title, url, author)

    return DiscordNotification(
        title=f"Issue #{number} {action}: {title}",
        url=url,
        description=f"{repo_name} by @{author}",
        color=_color_for_action(action, merged=False),
        agent_prompt=agent_prompt,
    )


def _issue_comment_notification(payload: dict[str, Any]) -> DiscordNotification | None:
    action = str(payload.get("action", ""))
    if action != "created":
        return None

    issue = _object(payload, "issue")
    if "pull_request" in issue:
        return None

    comment = _object(payload, "comment")
    repository = _object(payload, "repository")
    sender = _object(payload, "sender")

    number = int(issue["number"])
    title = str(issue["title"])
    url = str(issue["html_url"])
    repo_name = str(repository["full_name"])
    author = str(sender["login"])
    body = str(comment.get("body", ""))

    return DiscordNotification(
        title=f"Issue #{number} comment: {title}",
        url=url,
        description=f"{repo_name} by @{author}",
        color=0x0969DA,
        agent_prompt=_issue_refinement_prompt(repo_name, number, title, url, author, body),
    )


def _issue_refinement_prompt(
    repo_name: str,
    number: int,
    title: str,
    url: str,
    author: str,
    comment_body: str | None = None,
) -> str:
    prompt = (
        f"Refine StudyOS issue #{number} in {repo_name}: {title}\n"
        f"URL: {url}\n"
        f"Latest participant: @{author}\n"
        "Help the issue author shape this into actionable work. Ask clarifying questions, "
        "identify likely duplicates, suggest scope boundaries, and propose acceptance criteria. "
        "Only move toward implementation when the intended behavior and constraints are clear. "
        "If implementation is ready and the runtime has repository access, create a branch and PR. "
        "Never merge PRs; StudyOS students merge through GitHub."
    )
    if comment_body:
        prompt += f"\n\nLatest comment:\n{comment_body[:4000]}"
    return prompt


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
