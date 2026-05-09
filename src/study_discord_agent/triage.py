import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.github_client import GitHubClient, GitHubRef


def build_triage_prompt(
    repository: str,
    pull_requests: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> str:
    lines = [
        f"You are the StudyOS GitHub triage agent for {repository}.",
        "Inspect the current open PRs and issues below.",
        "Unify duplicate work, identify stale or blocked items, invite reviewers, and propose",
        "concrete next actions.",
        "For issues, ask refinement questions and suggest acceptance criteria before",
        "implementation.",
        "If the configured runtime has repository write access, you may implement clearly scoped",
        "issues by creating branches and pull requests.",
        "Never merge PRs. Merging is reserved for StudyOS students through GitHub.",
        "",
        "Open pull requests:",
    ]
    lines.extend(_item_lines(pull_requests))
    lines.append("")
    lines.append("Open issues:")
    lines.extend(_item_lines(issues))
    return "\n".join(lines)


async def run_github_triage_loop(
    settings: Settings,
    github: GitHubClient,
    agent: AgentGateway,
    publish: Callable[[str], Awaitable[None]],
) -> None:
    if not settings.github_repository:
        raise RuntimeError("GITHUB_REPOSITORY is required for polling")

    repo = GitHubRef.parse(settings.github_repository)
    while True:
        try:
            message = await run_once(settings, github, agent, repo)
            await publish(message)
        except Exception as exc:  # noqa: BLE001
            await publish(f"GitHub triage failed: {exc}")
        await asyncio.sleep(settings.github_poll_interval_seconds)


async def run_once(
    settings: Settings,
    github: GitHubClient,
    agent: AgentGateway,
    repo: GitHubRef,
) -> str:
    pull_requests, issues = await asyncio.gather(
        github.list_open_pull_requests(repo, settings.github_poll_limit),
        github.list_open_issues(repo, settings.github_poll_limit),
    )
    prompt = build_triage_prompt(settings.github_repository or "", pull_requests, issues)
    reply = await agent.ask(prompt=prompt, user="github-poller", channel_id=0)
    return reply.message[:1900]


def _item_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- None"]

    lines: list[str] = []
    for item in items:
        number = item.get("number", "?")
        title = item.get("title", "Untitled")
        url = item.get("html_url", "")
        updated_at = item.get("updated_at", "")
        user = item.get("user")
        user_data = cast(dict[str, Any], user) if isinstance(user, dict) else {}
        login_value = user_data.get("login")
        login = login_value if isinstance(login_value, str) else "unknown"
        lines.append(f"- #{number} {title} by @{login}, updated {updated_at}: {url}")
    return lines
