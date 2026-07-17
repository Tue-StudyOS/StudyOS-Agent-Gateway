from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from study_discord_agent.github_mirror_model import (
    GitHubHandledActionClaim,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorEvent,
    GitHubMirrorRecord,
    GitHubPendingAction,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


class ForeignItemKind(StrEnum):
    ISSUE = "issue"


class ForeignItemState(StrEnum):
    OPEN = "open"


class ForeignAction(StrEnum):
    REVIEW = "review"


def _event() -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id="delivery-enum",
        event_name="issues",
        action="opened",
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.ISSUE,
        item_number=12,
        item_url="https://github.com/Tue-StudyOS/example/issues/12",
        title="Question",
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=(),
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity="Issue opened",
        item_updated_at=NOW.isoformat(),
    )


def test_event_rejects_foreign_item_kind() -> None:
    with pytest.raises(ValueError, match="item_kind"):
        replace(
            _event(),
            item_kind=cast(GitHubItemKind, ForeignItemKind.ISSUE),
        )


def test_event_rejects_foreign_item_state() -> None:
    with pytest.raises(ValueError, match="state"):
        replace(
            _event(),
            state=cast(GitHubItemState, ForeignItemState.OPEN),
        )


@pytest.mark.parametrize("field", ["item_kind", "state"])
def test_record_rejects_foreign_enum_before_persistence(tmp_path: Path, field: str) -> None:
    path = tmp_path / f"{field}.json"
    store = GitHubMirrorStore(path, clock=lambda: NOW)
    record = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    before = path.read_bytes()

    def foreign_candidate(current: GitHubMirrorRecord) -> GitHubMirrorRecord:
        if field == "item_kind":
            return replace(
                current,
                item_kind=cast(GitHubItemKind, ForeignItemKind.ISSUE),
            )
        return replace(
            current,
            state=cast(GitHubItemState, ForeignItemState.OPEN),
        )

    with pytest.raises(ValueError, match=field):
        store.compare_and_set(record.mirror_id, record.revision, foreign_candidate)

    assert path.read_bytes() == before
    assert GitHubMirrorStore(path, clock=lambda: NOW).get(record.mirror_id) == record


def test_handled_claim_rejects_foreign_action_enum() -> None:
    with pytest.raises(ValueError, match="action"):
        GitHubHandledActionClaim(
            interaction_id=1,
            action=cast(GitHubMirrorAction, ForeignAction.REVIEW),
            task_id=str(uuid4()),
            succeeded=True,
        )


def test_pending_action_rejects_foreign_action_enum() -> None:
    with pytest.raises(ValueError, match="action"):
        GitHubPendingAction(
            interaction_id=1,
            action=cast(GitHubMirrorAction, ForeignAction.REVIEW),
            task_id=str(uuid4()),
            claimed_at=NOW.isoformat(),
        )
