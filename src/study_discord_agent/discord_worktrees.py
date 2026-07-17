import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.agent_execution_policy import (
    AgentExecutionPolicy,
    AgentPolicyClass,
)

logger = logging.getLogger(__name__)

REPO_NAME_PATTERN = r"[A-Za-z0-9._-]+"


@dataclass(frozen=True)
class DiscordWorkspace:
    path: Path
    repo_name: str | None = None
    canonical_path: Path | None = None
    commit_sha: str | None = None


class DiscordWorktreeManager:
    def __init__(
        self,
        worktree_root: str,
        canonical_root: str = "/workspaces/Tue-StudyOS",
        org_name: str = "Tue-StudyOS",
    ) -> None:
        self._worktree_root = Path(worktree_root)
        self._canonical_root = Path(canonical_root)
        self._org_name = org_name

    async def prepare(
        self,
        prompt: str,
        channel_id: int,
        *,
        repository_full_name: str | None = None,
        repository_commit_sha: str | None = None,
        execution_policy: AgentExecutionPolicy | None = None,
    ) -> DiscordWorkspace:
        channel_root = self._worktree_root / str(channel_id)
        if execution_policy is not None:
            if repository_full_name is None or repository_commit_sha is None:
                raise ValueError("Restricted execution requires repository and commit context")
            repo_name = self._repository_name_from_context(repository_full_name)
            return await self._prepare_restricted_workspace(
                channel_root,
                repo_name,
                repository_commit_sha,
                execution_policy,
            )
        if repository_full_name is not None:
            repo_name = self._repository_name_from_context(repository_full_name)
            return await self._prepare_repo_workspace(channel_root, repo_name)

        repo_names = extract_org_repo_names(prompt, self._org_name)
        if len(repo_names) != 1:
            if not repo_names:
                existing = await self._single_existing_repo_worktree(channel_root)
                if existing is not None:
                    return existing
            channel_root.mkdir(parents=True, exist_ok=True)
            return DiscordWorkspace(path=channel_root)

        return await self._prepare_repo_workspace(channel_root, repo_names[0])

    async def _prepare_restricted_workspace(
        self,
        channel_root: Path,
        repo_name: str,
        commit_sha: str,
        policy: AgentExecutionPolicy,
    ) -> DiscordWorkspace:
        canonical_path = self._canonical_root / repo_name
        await self._require_canonical_repo(canonical_path)
        pinned_sha = await _resolve_commit(canonical_path, commit_sha)
        if policy.policy_class is not AgentPolicyClass.IMPLEMENTATION:
            return DiscordWorkspace(
                path=canonical_path,
                repo_name=repo_name,
                canonical_path=canonical_path,
                commit_sha=pinned_sha,
            )
        worktree_path = channel_root / repo_name
        await self._ensure_worktree(canonical_path, worktree_path, pinned_sha)
        return DiscordWorkspace(
            path=worktree_path,
            repo_name=repo_name,
            canonical_path=canonical_path,
            commit_sha=pinned_sha,
        )

    @staticmethod
    async def _require_canonical_repo(canonical_path: Path) -> None:
        if not canonical_path.exists() or not await _is_git_worktree(canonical_path):
            raise RuntimeError("Restricted repository must already exist locally")

    async def _prepare_repo_workspace(
        self,
        channel_root: Path,
        repo_name: str,
    ) -> DiscordWorkspace:
        canonical_path = self._canonical_root / repo_name
        worktree_path = channel_root / repo_name
        await self._ensure_canonical_repo(repo_name, canonical_path)
        await self._ensure_worktree(canonical_path, worktree_path)
        return DiscordWorkspace(
            path=worktree_path,
            repo_name=repo_name,
            canonical_path=canonical_path,
        )

    def _repository_name_from_context(self, repository_full_name: str) -> str:
        owner, separator, repo_name = repository_full_name.partition("/")
        if (
            separator != "/"
            or owner != self._org_name
            or not repo_name
            or "/" in repo_name
            or repo_name in {".", ".."}
            or re.fullmatch(REPO_NAME_PATTERN, repo_name) is None
        ):
            raise ValueError("Discord repository context is invalid")
        return repo_name

    async def _single_existing_repo_worktree(
        self,
        channel_root: Path,
    ) -> DiscordWorkspace | None:
        if not channel_root.is_dir():
            return None

        workspaces: list[DiscordWorkspace] = []
        for child in channel_root.iterdir():
            if not child.is_dir() or not await _is_git_worktree(child):
                continue
            canonical_path = self._canonical_root / child.name
            workspaces.append(
                DiscordWorkspace(
                    path=child,
                    repo_name=child.name,
                    canonical_path=canonical_path if canonical_path.exists() else None,
                ),
            )
        if len(workspaces) != 1:
            return None
        return workspaces[0]

    async def _ensure_canonical_repo(self, repo_name: str, path: Path) -> None:
        if path.exists():
            if await _is_git_worktree(path):
                return
            raise RuntimeError(f"Canonical repository path is not a Git repository: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["GH_CONFIG_DIR"] = env.get("GH_STUDYOS_ORG_CONFIG_DIR", "/auth/gh-studyos-org")
        await _run_checked(
            ["gh", "repo", "clone", f"{self._org_name}/{repo_name}", str(path)],
            env=env,
        )

    async def _ensure_worktree(
        self,
        canonical_path: Path,
        worktree_path: Path,
        commit_sha: str = "HEAD",
    ) -> None:
        if worktree_path.exists():
            if await _is_git_worktree(worktree_path):
                if commit_sha != "HEAD":
                    existing_sha = await _current_commit(worktree_path)
                    if existing_sha != commit_sha:
                        raise RuntimeError(
                            "Existing restricted worktree is pinned to another commit",
                        )
                return
            if any(worktree_path.iterdir()):
                raise RuntimeError(
                    f"Discord worktree path exists and is not empty: {worktree_path}",
                )
        else:
            worktree_path.parent.mkdir(parents=True, exist_ok=True)

        await _run_checked(
            [
                "git",
                "-C",
                str(canonical_path),
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                commit_sha,
            ],
        )


def extract_org_repo_names(text: str, org_name: str = "Tue-StudyOS") -> tuple[str, ...]:
    patterns = (
        rf"github\.com[:/]{re.escape(org_name)}/({REPO_NAME_PATTERN})(?:\.git)?",
        rf"\b{re.escape(org_name)}/({REPO_NAME_PATTERN})(?:\.git)?\b",
    )
    repo_names: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            name = _normalize_repo_name(match.group(1))
            if name and name not in repo_names:
                repo_names.append(name)
    return tuple(repo_names)


def _normalize_repo_name(value: str) -> str:
    return value.removesuffix(".git").rstrip(".,;:)").strip()


async def _is_git_worktree(path: Path) -> bool:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        "rev-parse",
        "--is-inside-work-tree",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    return process.returncode == 0 and stdout.decode().strip() == "true"


async def _resolve_commit(path: Path, commit_sha: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40,64}", commit_sha) is None:
        raise ValueError("Restricted repository commit is invalid")
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        "rev-parse",
        "--verify",
        f"{commit_sha}^{{commit}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    resolved = stdout.decode().strip()
    if process.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", resolved) is None:
        raise RuntimeError("Restricted repository commit is unavailable locally")
    return resolved


async def _current_commit(path: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    resolved = stdout.decode().strip()
    if process.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", resolved) is None:
        raise RuntimeError("Existing Discord worktree commit cannot be resolved")
    return resolved


async def _run_checked(args: list[str], env: dict[str, str] | None = None) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace").strip()
        output = stdout.decode("utf-8", errors="replace").strip()
        details = error or output or "no output"
        raise RuntimeError(f"Command failed: {' '.join(args)}: {details[:1000]}")
    logger.info("prepared Discord worktree command=%s", " ".join(args))
