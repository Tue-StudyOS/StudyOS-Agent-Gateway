from dataclasses import dataclass
from typing import Any, cast

import httpx


class GitHubWriteDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRef:
    owner: str
    repo: str

    @classmethod
    def parse(cls, value: str) -> "GitHubRef":
        owner, repo = value.split("/", maxsplit=1)
        return cls(owner=owner, repo=repo)


class GitHubClient:
    def __init__(self, token: str | None, write_enabled: bool) -> None:
        self._token = token
        self._write_enabled = write_enabled

    async def comment_on_issue(self, repo: GitHubRef, number: int, body: str) -> str:
        self._require_write()
        data = await self._request(
            "POST",
            f"/repos/{repo.owner}/{repo.repo}/issues/{number}/comments",
            json={"body": body},
        )
        return str(data["html_url"])

    async def close_issue(self, repo: GitHubRef, number: int) -> str:
        self._require_write()
        data = await self._request(
            "PATCH",
            f"/repos/{repo.owner}/{repo.repo}/issues/{number}",
            json={"state": "closed"},
        )
        return str(data["html_url"])

    async def merge_pull_request(
        self,
        repo: GitHubRef,
        number: int,
        commit_title: str | None = None,
    ) -> str:
        self._require_write()
        payload: dict[str, str] = {"merge_method": "squash"}
        if commit_title:
            payload["commit_title"] = commit_title
        data = await self._request(
            "PUT",
            f"/repos/{repo.owner}/{repo.repo}/pulls/{number}/merge",
            json=payload,
        )
        return str(data.get("sha", "merged"))

    def _require_write(self) -> None:
        if not self._write_enabled:
            raise GitHubWriteDisabledError("GitHub write actions are disabled")
        if not self._token:
            raise GitHubWriteDisabledError("GITHUB_TOKEN is required for write actions")

    async def _request(self, method: str, path: str, json: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(base_url="https://api.github.com", timeout=20) as client:
            response = await client.request(method, path, headers=headers, json=json)
            response.raise_for_status()
            data = cast(object, response.json())
        if not isinstance(data, dict):
            raise RuntimeError("GitHub returned a non-object response")
        return cast(dict[str, Any], data)
