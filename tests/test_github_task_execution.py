import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from study_discord_agent.agent_errors import AgentConfigurationError
from study_discord_agent.agent_execution_policy import AgentPolicyClass
from study_discord_agent.discord_task_model import (
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.github_task_context import resolve_github_task_context
from study_discord_agent.github_task_execution import GitHubTaskExecutionResolver

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(root: Path) -> tuple[Path, str, str]:
    repository = root / "example"
    repository.mkdir(parents=True)
    subprocess.run(
        ("git", "init", "-q", "-b", "main", str(repository)),
        check=True,
    )
    _git(repository, "config", "user.email", "tests@studyos.invalid")
    _git(repository, "config", "user.name", "StudyOS Tests")
    tracked = repository / "tracked.txt"
    tracked.write_text("first\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "first")
    first = _git(repository, "rev-parse", "HEAD")
    tracked.write_text("second\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "second")
    return repository, first, _git(repository, "rev-parse", "HEAD")


def _event(head_sha: str, *, owner: str = "Tue-StudyOS") -> GitHubMirrorEvent:
    repository = f"{owner}/example"
    return GitHubMirrorEvent(
        delivery_id=f"delivery-{owner}-{head_sha[:8]}",
        event_name="pull_request",
        action="opened",
        repository_full_name=repository,
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=7,
        item_url=f"https://github.com/{repository}/pull/7",
        title="Example change",
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=(),
        base_ref="main",
        head_ref="feature",
        base_sha=head_sha,
        head_sha=head_sha,
        activity="Pull request opened",
        item_updated_at=NOW.isoformat(),
    )


def _task(
    intent: DiscordTaskIntent,
    *,
    source_reference_id: str | None,
    repository_commit_sha: str | None,
) -> DiscordTaskRecord:
    return DiscordTaskRecord(
        task_id=str(uuid4()),
        revision=0,
        owner_id=1,
        guild_id=2,
        origin_channel_id=3,
        execution_channel_id=4,
        trigger_event_id=5,
        source_message_id=6,
        card_message_id=None,
        result_message_id=None,
        source_kind=DiscordTaskSourceKind.CONTEXT_ACTION,
        source_label="GitHub action",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        attempt=1,
        state=DiscordTaskState.STARTING,
        intent=intent,
        source_reference_id=source_reference_id,
        repository_commit_sha=repository_commit_sha,
    )


@pytest.mark.asyncio
async def test_context_pins_mirrored_head_and_can_rehydrate_exact_persisted_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "canonical"
    _, first, second = _repository(root)
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    mirror = store.upsert_event(_event(first), guild_id=10, channel_id=20).record

    initial = await resolve_github_task_context(mirror, root)
    rehydrated = await resolve_github_task_context(
        replace(mirror, head_sha=second, base_sha=second),
        root,
        pinned_commit_sha=first,
    )

    assert initial.commit_sha == first
    assert rehydrated.commit_sha == first


@pytest.mark.asyncio
async def test_resolver_rehydrates_source_and_maps_exact_policy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "canonical"
    _, first, latest = _repository(root)
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    mirror = store.upsert_event(_event(latest), guild_id=10, channel_id=20).record
    mappings = {
        DiscordTaskIntent.REVIEW: AgentPolicyClass.REVIEW,
        DiscordTaskIntent.SECURITY_REVIEW: AgentPolicyClass.SECURITY_REVIEW,
        DiscordTaskIntent.VULNERABILITY_SCAN: AgentPolicyClass.VULNERABILITY_SCAN,
        DiscordTaskIntent.IMPLEMENTATION: AgentPolicyClass.IMPLEMENTATION,
    }

    for intent, policy_class in mappings.items():
        context = await GitHubTaskExecutionResolver(store, root)(
            _task(
                intent,
                source_reference_id=mirror.mirror_id,
                repository_commit_sha=first,
            )
        )

        assert context.channel_id == 4
        assert context.trigger_event_id == 5
        assert context.repository_full_name == "Tue-StudyOS/example"
        assert context.repository_commit_sha == first
        assert context.execution_policy is not None
        assert context.execution_policy.policy_class is policy_class


@pytest.mark.asyncio
async def test_resolver_leaves_general_tasks_unrestricted(tmp_path: Path) -> None:
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)

    context = await GitHubTaskExecutionResolver(store, tmp_path / "missing")(
        _task(
            DiscordTaskIntent.GENERAL,
            source_reference_id=None,
            repository_commit_sha=None,
        )
    )

    assert context.repository_full_name is None
    assert context.repository_commit_sha is None
    assert context.execution_policy is None


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_missing_or_mismatched_persisted_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "canonical"
    _, first, _ = _repository(root)
    store = GitHubMirrorStore(tmp_path / "mirrors.json", clock=lambda: NOW)
    mirror = store.upsert_event(_event(first), guild_id=10, channel_id=20).record
    resolve = GitHubTaskExecutionResolver(store, root)

    records = (
        _task(
            DiscordTaskIntent.REVIEW,
            source_reference_id=None,
            repository_commit_sha=first,
        ),
        _task(
            DiscordTaskIntent.REVIEW,
            source_reference_id="f" * 32,
            repository_commit_sha=first,
        ),
        _task(
            DiscordTaskIntent.REVIEW,
            source_reference_id=mirror.mirror_id,
            repository_commit_sha="f" * 40,
        ),
    )
    for record in records:
        with pytest.raises(AgentConfigurationError):
            await resolve(record)

    foreign = store.upsert_event(
        _event(first, owner="Outside-Org"), guild_id=10, channel_id=20
    ).record
    with pytest.raises(AgentConfigurationError, match="Tue-StudyOS"):
        await resolve(
            _task(
                DiscordTaskIntent.REVIEW,
                source_reference_id=foreign.mirror_id,
                repository_commit_sha=first,
            )
        )
