import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubMirrorRecord,
)

_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class GitHubTaskContext:
    mirror_id: str
    repository_full_name: str
    commit_sha: str
    base_sha: str | None
    item_kind: GitHubItemKind
    item_number: int
    item_url: str


async def resolve_github_task_context(
    record: GitHubMirrorRecord,
    canonical_root: Path,
) -> GitHubTaskContext:
    owner, separator, repo_name = record.repository_full_name.partition("/")
    if separator != "/" or owner != "Tue-StudyOS" or not repo_name:
        raise RuntimeError("Only local Tue-StudyOS repositories can run GitHub actions")
    root = canonical_root.expanduser().resolve()
    repository = (root / repo_name).resolve()
    if repository.parent != root or not repository.is_dir():
        raise RuntimeError("The mirrored repository is not available locally")
    if record.item_kind is GitHubItemKind.PULL_REQUEST:
        if record.head_sha is None:
            raise RuntimeError("This pull request has no pinned head commit")
        requested = record.head_sha
    else:
        requested = "HEAD"
    commit_sha = await _resolve_commit(repository, requested)
    if record.base_sha is not None:
        await _resolve_commit(repository, record.base_sha)
    return GitHubTaskContext(
        mirror_id=record.mirror_id,
        repository_full_name=record.repository_full_name,
        commit_sha=commit_sha,
        base_sha=record.base_sha,
        item_kind=record.item_kind,
        item_number=record.item_number,
        item_url=record.item_url,
    )


async def _resolve_commit(repository: Path, revision: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repository),
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    resolved = stdout.decode().strip()
    if process.returncode != 0 or _SHA.fullmatch(resolved) is None:
        raise RuntimeError("The pinned GitHub commit is not available locally")
    return resolved
