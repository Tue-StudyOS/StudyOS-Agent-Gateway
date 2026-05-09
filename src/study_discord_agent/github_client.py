import asyncio
from dataclasses import dataclass
from typing import Any, cast

import httpx


@dataclass(frozen=True)
class GitHubRef:
    owner: str
    repo: str

    @classmethod
    def parse(cls, value: str) -> "GitHubRef":
        owner, repo = value.split("/", maxsplit=1)
        return cls(owner=owner, repo=repo)


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self._token = token

    async def list_open_pull_requests(
        self,
        repo: GitHubRef,
        limit: int,
    ) -> list[dict[str, Any]]:
        data = await self._request_list(
            "GET",
            f"/repos/{repo.owner}/{repo.repo}/pulls",
            params={"state": "open", "per_page": str(limit), "sort": "updated"},
        )
        return cast(list[dict[str, Any]], data)

    async def list_open_issues(
        self,
        repo: GitHubRef,
        limit: int,
    ) -> list[dict[str, Any]]:
        data = await self._request_list(
            "GET",
            f"/repos/{repo.owner}/{repo.repo}/issues",
            params={"state": "open", "per_page": str(limit), "sort": "updated"},
        )
        issues = [item for item in data if "pull_request" not in item]
        return cast(list[dict[str, Any]], issues)

    async def _request_object(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data = await self._request(method, path, json=json, params=params)
        if not isinstance(data, dict):
            raise RuntimeError("GitHub returned a non-object response")
        return data

    async def _request_list(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> list[Any]:
        data = await self._request(method, path, json=json, params=params)
        if not isinstance(data, list):
            raise RuntimeError("GitHub returned a non-list response")
        return data

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = await self._auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(base_url="https://api.github.com", timeout=20) as client:
            response = await client.request(
                method,
                path,
                headers=headers,
                json=json,
                params=params,
            )
            response.raise_for_status()
            data = cast(object, response.json())
        if not isinstance(data, dict | list):
            raise RuntimeError("GitHub returned a non-object response")
        return cast(dict[str, Any] | list[Any], data)

    async def _auth_token(self) -> str | None:
        if self._token:
            return self._token
        try:
            process = await asyncio.create_subprocess_exec(
                "gh",
                "auth",
                "token",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return None

        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
            return None

        if process.returncode != 0:
            return None
        token = stdout.decode("utf-8", errors="replace").strip()
        return token or None
