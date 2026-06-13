import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_NAME_PATTERN = r"[A-Za-z0-9._-]+"


@dataclass(frozen=True)
class DiscordWorkspace:
    path: Path
    repo_name: str | None = None
    canonical_path: Path | None = None


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

    async def prepare(self, prompt: str, channel_id: int) -> DiscordWorkspace:
        channel_root = self._worktree_root / str(channel_id)
        repo_names = extract_org_repo_names(prompt, self._org_name)
        if len(repo_names) != 1:
            channel_root.mkdir(parents=True, exist_ok=True)
            return DiscordWorkspace(path=channel_root)

        repo_name = repo_names[0]
        canonical_path = self._canonical_root / repo_name
        worktree_path = channel_root / repo_name
        await self._ensure_canonical_repo(repo_name, canonical_path)
        await self._ensure_worktree(canonical_path, worktree_path)
        return DiscordWorkspace(
            path=worktree_path,
            repo_name=repo_name,
            canonical_path=canonical_path,
        )

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

    async def _ensure_worktree(self, canonical_path: Path, worktree_path: Path) -> None:
        if worktree_path.exists():
            if await _is_git_worktree(worktree_path):
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
                "HEAD",
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
