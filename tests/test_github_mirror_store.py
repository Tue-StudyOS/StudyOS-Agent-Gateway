import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError
from study_discord_agent.github_mirror_model import (
    GitHubHandledActionClaim,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorEvent,
    GitHubPendingAction,
)
from study_discord_agent.github_mirror_store import (
    DELIVERY_ID_LIMIT,
    HANDLED_CLAIM_LIMIT,
    GitHubDeliveryCollision,
    GitHubMirrorRevisionConflict,
    GitHubMirrorStore,
    GitHubMirrorStoreCorruptionError,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _event(
    delivery_id: str = "delivery-1",
    *,
    title: str = "Initial title",
    updated_at: datetime = NOW,
) -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name="pull_request",
        action="opened",
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=7,
        item_url="https://github.com/Tue-StudyOS/example/pull/7",
        title=title,
        state=GitHubItemState.OPEN,
        author_login="student",
        labels=("backend",),
        base_ref="main",
        head_ref="feature",
        base_sha="b" * 40,
        head_sha="a" * 40,
        activity="Pull request opened",
        item_updated_at=updated_at.isoformat(),
    )


def _store(tmp_path: Path) -> GitHubMirrorStore:
    return GitHubMirrorStore(tmp_path / "github-mirrors.json", clock=lambda: NOW)


def test_logical_upsert_deduplicates_delivery_and_rejects_collision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.upsert_event(_event(), guild_id=10, channel_id=20)
    duplicate = store.upsert_event(_event(), guild_id=10, channel_id=20)
    updated = store.upsert_event(
        _event("delivery-2", title="Updated title", updated_at=NOW + timedelta(seconds=1)),
        guild_id=10,
        channel_id=20,
    )

    assert not created.duplicate
    assert duplicate.duplicate
    assert duplicate.record.revision == created.record.revision
    assert updated.record.mirror_id == created.record.mirror_id
    assert updated.record.title == "Updated title"
    assert updated.record.recent_delivery_ids == ("delivery-1", "delivery-2")
    assert len(updated.record.mirror_id) == 32
    int(updated.record.mirror_id, 16)

    other = replace(
        _event(), item_number=8, item_url="https://github.com/Tue-StudyOS/example/pull/8"
    )
    with pytest.raises(GitHubDeliveryCollision):
        store.upsert_event(other, guild_id=10, channel_id=20)


def test_stale_event_cannot_regress_metadata_or_pinned_revisions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    current = store.upsert_event(
        _event("new", title="Current", updated_at=NOW + timedelta(minutes=1)),
        guild_id=10,
        channel_id=20,
    ).record
    stale_event = replace(
        _event("old", title="Old", updated_at=NOW),
        base_sha=None,
        head_sha=None,
    )

    stale = store.upsert_event(stale_event, guild_id=10, channel_id=20).record

    assert stale.title == "Current"
    assert stale.base_sha == current.base_sha
    assert stale.head_sha == current.head_sha
    assert stale.recent_delivery_ids == ("new", "old")


def test_comment_activity_cannot_downgrade_pr_specific_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    merged = store.upsert_event(
        replace(
            _event("merged", updated_at=NOW),
            state=GitHubItemState.MERGED,
            action="closed",
        ),
        guild_id=10,
        channel_id=20,
    ).record
    comment = replace(
        _event("comment", updated_at=NOW + timedelta(seconds=1)),
        event_name="issue_comment",
        action="created",
        state=GitHubItemState.CLOSED,
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity="Comment created",
    )

    updated = store.upsert_event(comment, guild_id=10, channel_id=20).record

    assert updated.state is GitHubItemState.MERGED
    assert updated.base_sha == merged.base_sha
    assert updated.head_sha == merged.head_sha
    assert updated.activity == "Comment created"


def test_store_persists_strict_private_bounded_metadata_and_reloads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    claims = tuple(
        GitHubHandledActionClaim(
            interaction_id=index + 1,
            action=GitHubMirrorAction.REVIEW,
            task_id=str(uuid4()),
            succeeded=True,
        )
        for index in range(HANDLED_CLAIM_LIMIT + 3)
    )
    record = store.compare_and_set(
        record.mirror_id,
        record.revision,
        lambda current: replace(
            current,
            handled_interaction_claims=claims,
            thread_id=30,
            active_task_id=str(uuid4()),
        ),
    )
    for index in range(DELIVERY_ID_LIMIT + 3):
        record = store.upsert_event(
            _event(f"delivery-extra-{index}", updated_at=NOW + timedelta(seconds=index + 1)),
            guild_id=10,
            channel_id=20,
        ).record

    path = tmp_path / "github-mirrors.json"
    document = path.read_text(encoding="utf-8")
    payload = json.loads(document)
    assert set(payload) == {"version", "mirrors"}
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert len(record.recent_delivery_ids) == DELIVERY_ID_LIMIT
    assert len(record.handled_interaction_claims) == HANDLED_CLAIM_LIMIT
    for forbidden in (
        "secret",
        "body",
        "comment",
        "prompt",
        "result",
        "raw_error",
        "modal",
        "actor_id",
    ):
        assert forbidden not in document
    reloaded = GitHubMirrorStore(path, clock=lambda: NOW).get(record.mirror_id)
    assert reloaded == record


def test_card_compare_and_set_helpers_preserve_task_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    attached, won = store.attach_card_if_missing(record.mirror_id, 99)
    raced, second_won = store.attach_card_if_missing(record.mirror_id, 100)
    retained = store.upsert_event(
        _event("delivery-2", updated_at=NOW + timedelta(seconds=1)),
        guild_id=10,
        channel_id=20,
    ).record
    cleared = store.clear_card_if_matches(record.mirror_id, 99)

    assert won and attached.card_message_id == 99
    assert not second_won and raced.card_message_id == 99
    assert retained.card_message_id == 99
    assert cleared.card_message_id is None
    with pytest.raises(GitHubMirrorRevisionConflict):
        store.compare_and_set(record.mirror_id, 0, lambda current: current)


def test_operational_references_are_opaque_and_survive_webhook_upsert(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    task_id = str(uuid4())
    pending = GitHubPendingAction(
        interaction_id=123,
        action=GitHubMirrorAction.WORK,
        task_id=task_id,
        claimed_at=NOW.isoformat(),
    )
    claimed = store.compare_and_set(
        record.mirror_id,
        record.revision,
        lambda current: replace(
            current,
            pending_action=pending,
            active_task_id=task_id,
            thread_id=30,
        ),
    )

    retained = store.upsert_event(
        _event("delivery-2", updated_at=NOW + timedelta(seconds=1)),
        guild_id=10,
        channel_id=20,
    ).record

    assert retained.pending_action == pending
    assert retained.active_task_id == task_id
    assert retained.thread_id == 30
    with pytest.raises(ValueError, match="opaque"):
        replace(claimed, active_task_id="copied prompt or secret")


def test_typed_event_rejects_unsupported_action() -> None:
    with pytest.raises(ValueError, match="action"):
        replace(_event(), action="assigned")


def test_atomic_failure_and_post_replace_durability_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import study_discord_agent.discord_task_persistence as persistence

    store = _store(tmp_path)
    record = store.upsert_event(_event(), guild_id=10, channel_id=20).record
    before = (tmp_path / "github-mirrors.json").read_bytes()

    def fail_replace(*_: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(persistence.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk"):
        store.attach_card_if_missing(record.mirror_id, 99)
    assert store.get(record.mirror_id).card_message_id is None
    assert (tmp_path / "github-mirrors.json").read_bytes() == before

    monkeypatch.undo()

    def fail_directory_sync(*_: object) -> None:
        raise OSError("sync")

    monkeypatch.setattr(persistence, "_fsync_directory", fail_directory_sync)
    with pytest.raises(TaskStoreDurabilityError):
        store.attach_card_if_missing(record.mirror_id, 99)
    assert store.get(record.mirror_id).card_message_id == 99


def test_corrupt_or_unknown_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "github-mirrors.json"
    path.write_text('{"version":1,"mirrors":{},"body":"not allowed"}', encoding="utf-8")
    with pytest.raises(GitHubMirrorStoreCorruptionError):
        GitHubMirrorStore(path)
