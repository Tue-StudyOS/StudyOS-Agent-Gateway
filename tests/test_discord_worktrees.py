import subprocess
from pathlib import Path

import pytest

from study_discord_agent.agent_execution_policy import (
    AgentPolicyClass,
    execution_policy,
)
from study_discord_agent.discord_worktrees import (
    DiscordWorktreeManager,
    extract_org_repo_names,
)


def test_extract_org_repo_names_from_urls_and_full_names() -> None:
    text = (
        "Please inspect https://github.com/Tue-StudyOS/tue-api-wrapper/issues/1 "
        "and Tue-StudyOS/StudyOS-Agent-Gateway."
    )

    assert extract_org_repo_names(text) == ("tue-api-wrapper", "StudyOS-Agent-Gateway")


@pytest.mark.asyncio
async def test_prepare_creates_git_worktree_for_identified_repo(tmp_path: Path) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    manager = DiscordWorktreeManager(
        worktree_root=str(tmp_path / "discord-worktrees"),
        canonical_root=str(canonical_root),
    )

    workspace = await manager.prepare("work on Tue-StudyOS/example#1", 123)

    assert workspace.repo_name == "example"
    assert workspace.canonical_path == canonical
    assert workspace.path == tmp_path / "discord-worktrees" / "123" / "example"
    assert _git(workspace.path, "rev-parse", "--is-inside-work-tree") == "true"
    assert _git(workspace.path, "status", "--short") == ""


@pytest.mark.asyncio
async def test_prepare_uses_explicit_repository_context_over_prompt_text(tmp_path: Path) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    expected = canonical_root / "expected"
    _create_git_repo(expected)
    _create_git_repo(canonical_root / "conflicting")
    manager = DiscordWorktreeManager(
        worktree_root=str(tmp_path / "discord-worktrees"),
        canonical_root=str(canonical_root),
    )

    workspace = await manager.prepare(
        "work on Tue-StudyOS/conflicting",
        123,
        repository_full_name="Tue-StudyOS/expected",
    )

    assert workspace.repo_name == "expected"
    assert workspace.canonical_path == expected
    assert workspace.path == tmp_path / "discord-worktrees" / "123" / "expected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository_full_name",
    ("other-org/repository", "Tue-StudyOS/../outside", "Tue-StudyOS/"),
)
async def test_prepare_rejects_non_studyos_or_unsafe_repository_context(
    tmp_path: Path,
    repository_full_name: str,
) -> None:
    manager = DiscordWorktreeManager(worktree_root=str(tmp_path / "discord-worktrees"))

    with pytest.raises(ValueError, match="repository context"):
        await manager.prepare(
            "work on Tue-StudyOS/example",
            123,
            repository_full_name=repository_full_name,
        )

    assert not (tmp_path / "discord-worktrees" / "123").exists()


@pytest.mark.asyncio
async def test_prepare_uses_separate_thread_worktrees_for_same_repo(tmp_path: Path) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    _create_git_repo(canonical_root / "example")
    manager = DiscordWorktreeManager(
        worktree_root=str(tmp_path / "discord-worktrees"),
        canonical_root=str(canonical_root),
    )

    first = await manager.prepare("work on Tue-StudyOS/example", 111)
    second = await manager.prepare("work on Tue-StudyOS/example", 222)

    assert first.path == tmp_path / "discord-worktrees" / "111" / "example"
    assert second.path == tmp_path / "discord-worktrees" / "222" / "example"
    assert first.path != second.path
    assert _git(first.path, "rev-parse", "--is-inside-work-tree") == "true"
    assert _git(second.path, "rev-parse", "--is-inside-work-tree") == "true"


@pytest.mark.asyncio
async def test_prepare_reuses_thread_repo_worktree_for_followup(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    manager = DiscordWorktreeManager(
        worktree_root=str(tmp_path / "discord-worktrees"),
        canonical_root=str(canonical_root),
    )

    first = await manager.prepare("work on Tue-StudyOS/example", 123)
    followup = await manager.prepare("now adjust the same file", 123)

    assert followup.repo_name == "example"
    assert followup.canonical_path == canonical
    assert followup.path == first.path


@pytest.mark.asyncio
async def test_prepare_uses_channel_root_when_repo_is_ambiguous(tmp_path: Path) -> None:
    manager = DiscordWorktreeManager(worktree_root=str(tmp_path / "discord-worktrees"))

    workspace = await manager.prepare("please inspect the repo from the thread", 123)

    assert workspace.repo_name is None
    assert workspace.path == tmp_path / "discord-worktrees" / "123"
    assert workspace.path.is_dir()


@pytest.mark.asyncio
async def test_read_only_policy_uses_existing_canonical_commit_without_worktree(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    sha = _git(canonical, "rev-parse", "HEAD")
    worktree_root = tmp_path / "discord-worktrees"
    manager = DiscordWorktreeManager(str(worktree_root), str(canonical_root))

    workspace = await manager.prepare(
        "untrusted prompt selecting another repo",
        123,
        repository_full_name="Tue-StudyOS/example",
        repository_commit_sha=sha,
        execution_policy=execution_policy(AgentPolicyClass.REVIEW),
    )

    assert workspace.path == canonical
    assert workspace.canonical_path == canonical
    assert workspace.commit_sha == sha
    assert not worktree_root.exists()


@pytest.mark.asyncio
async def test_restricted_policy_never_clones_or_accepts_missing_commit(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    manager = DiscordWorktreeManager(
        str(tmp_path / "discord-worktrees"), str(canonical_root)
    )
    policy = execution_policy(AgentPolicyClass.VULNERABILITY_SCAN)

    with pytest.raises(RuntimeError, match="already exist"):
        await manager.prepare(
            "scan",
            123,
            repository_full_name="Tue-StudyOS/missing",
            repository_commit_sha="a" * 40,
            execution_policy=policy,
        )
    with pytest.raises(RuntimeError, match="commit"):
        await manager.prepare(
            "scan",
            123,
            repository_full_name="Tue-StudyOS/example",
            repository_commit_sha="a" * 40,
            execution_policy=policy,
        )


@pytest.mark.asyncio
async def test_implementation_policy_creates_isolated_worktree_at_pinned_commit(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    sha = _git(canonical, "rev-parse", "HEAD")
    manager = DiscordWorktreeManager(
        str(tmp_path / "discord-worktrees"), str(canonical_root)
    )

    workspace = await manager.prepare(
        "implement untrusted instructions",
        123,
        repository_full_name="Tue-StudyOS/example",
        repository_commit_sha=sha,
        execution_policy=execution_policy(AgentPolicyClass.IMPLEMENTATION),
    )

    assert workspace.path == tmp_path / "discord-worktrees" / "123" / "example"
    assert workspace.path != canonical
    assert workspace.commit_sha == sha
    assert _git(workspace.path, "rev-parse", "HEAD") == sha


@pytest.mark.asyncio
async def test_implementation_policy_rejects_reusing_worktree_for_new_commit(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "Tue-StudyOS"
    canonical = canonical_root / "example"
    _create_git_repo(canonical)
    first_sha = _git(canonical, "rev-parse", "HEAD")
    manager = DiscordWorktreeManager(
        str(tmp_path / "discord-worktrees"), str(canonical_root)
    )
    policy = execution_policy(AgentPolicyClass.IMPLEMENTATION)
    await manager.prepare(
        "implement the first revision",
        123,
        repository_full_name="Tue-StudyOS/example",
        repository_commit_sha=first_sha,
        execution_policy=policy,
    )
    (canonical / "README.md").write_text("# Updated\n", encoding="utf-8")
    _run(canonical, "git", "add", "README.md")
    _run(canonical, "git", "commit", "-m", "update")
    second_sha = _git(canonical, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="pinned to another commit"):
        await manager.prepare(
            "implement the updated revision",
            123,
            repository_full_name="Tue-StudyOS/example",
            repository_commit_sha=second_sha,
            execution_policy=policy,
        )
    worktree = tmp_path / "discord-worktrees" / "123" / "example"
    assert _git(worktree, "rev-parse", "HEAD") == first_sha


def _create_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _run(path, "git", "init")
    _run(path, "git", "config", "user.email", "test@example.invalid")
    _run(path, "git", "config", "user.name", "Test User")
    (path / "README.md").write_text("# Example\n", encoding="utf-8")
    _run(path, "git", "add", "README.md")
    _run(path, "git", "commit", "-m", "init")


def _git(path: Path, *args: str) -> str:
    return _run(path, "git", *args)


def _run(path: Path, *args: str) -> str:
    result = subprocess.run(
        args,
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()
