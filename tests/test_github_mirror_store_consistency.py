import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread
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
)
from study_discord_agent.github_mirror_store import (
    GitHubMirrorRevisionConflict,
    GitHubMirrorStore,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def _event(
    delivery_id: str,
    *,
    state: GitHubItemState = GitHubItemState.OPEN,
    action: str = "opened",
    title: str = "Current title",
    updated_at: datetime = NOW,
) -> GitHubMirrorEvent:
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name="pull_request",
        action=action,
        repository_full_name="Tue-StudyOS/example",
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=7,
        item_url="https://github.com/Tue-StudyOS/example/pull/7",
        title=title,
        state=state,
        author_login="student",
        labels=("backend",),
        base_ref="main",
        head_ref="feature",
        base_sha="b" * 40,
        head_sha="a" * 40,
        activity=f"Pull request {action}",
        item_updated_at=updated_at.isoformat(),
    )


def _store(path: Path) -> GitHubMirrorStore:
    return GitHubMirrorStore(path, clock=lambda: NOW)


@pytest.mark.parametrize("terminal_state", [GitHubItemState.CLOSED, GitHubItemState.MERGED])
def test_equal_timestamp_open_event_cannot_regress_terminal_state(
    tmp_path: Path, terminal_state: GitHubItemState
) -> None:
    store = _store(tmp_path / f"{terminal_state.value}.json")
    terminal = store.upsert_event(
        _event(
            "terminal",
            state=terminal_state,
            action="closed",
            title="Terminal title",
        ),
        guild_id=10,
        channel_id=20,
    ).record

    delayed = store.upsert_event(
        _event("delayed", action="synchronize", title="Stale title"),
        guild_id=10,
        channel_id=20,
    ).record

    assert delayed.state is terminal_state
    assert delayed.title == terminal.title


def test_reopen_must_be_strictly_newer_than_closed_state(tmp_path: Path) -> None:
    store = _store(tmp_path / "reopen.json")
    closed = store.upsert_event(
        _event("closed", state=GitHubItemState.CLOSED, action="closed"),
        guild_id=10,
        channel_id=20,
    ).record

    equal = store.upsert_event(
        _event("equal-reopen", action="reopened"), guild_id=10, channel_id=20
    ).record
    newer = store.upsert_event(
        _event("newer-reopen", action="reopened", updated_at=NOW + timedelta(seconds=1)),
        guild_id=10,
        channel_id=20,
    ).record

    assert equal.state is closed.state
    assert newer.state is GitHubItemState.OPEN


def test_two_store_instances_serialize_concurrent_upserts_and_refresh_reads(
    tmp_path: Path,
) -> None:
    path = tmp_path / "github-mirrors.json"
    first = _store(path)
    second = _store(path)
    ready = Barrier(2)

    def upsert(store: GitHubMirrorStore, event: GitHubMirrorEvent) -> GitHubMirrorRecord:
        ready.wait()
        return store.upsert_event(event, guild_id=10, channel_id=20).record

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(upsert, first, _event("first"))
        second_future = executor.submit(
            upsert,
            second,
            _event("second", title="Updated title", updated_at=NOW + timedelta(seconds=1)),
        )
        first_result = first_future.result(timeout=2)
        second_result = second_future.result(timeout=2)

    assert second_result.mirror_id == first_result.mirror_id
    canonical = first.get(first_result.mirror_id)
    assert canonical.title == "Updated title"
    assert first.records() == (canonical,)
    assert os.stat(path.with_name(f"{path.name}.lock")).st_mode & 0o777 == 0o600


def test_two_store_instances_enforce_single_card_winner_and_cas(tmp_path: Path) -> None:
    path = tmp_path / "github-mirrors.json"
    first = _store(path)
    created = first.upsert_event(_event("first"), guild_id=10, channel_id=20).record
    claimed, claim_won = first.claim_card_creation(created.mirror_id)
    assert claim_won and claimed.card_create_nonce is not None
    second = _store(path)

    winner, attached = first.attach_card_if_missing(
        created.mirror_id, 101, claimed.card_create_nonce
    )
    retained, raced = second.attach_card_if_missing(
        created.mirror_id, 202, claimed.card_create_nonce
    )

    assert attached
    assert not raced
    assert retained.card_message_id == winner.card_message_id == 101
    assert second.get(created.mirror_id).card_message_id == 101
    with pytest.raises(GitHubMirrorRevisionConflict):
        second.compare_and_set(
            created.mirror_id,
            created.revision,
            lambda record: replace(record, thread_id=303),
        )


@pytest.mark.parametrize("invalid", ["prompt-modal-secret-value", 1, object()])
def test_handled_claim_rejects_non_boolean_before_persistence(
    tmp_path: Path, invalid: object
) -> None:
    path = tmp_path / "github-mirrors.json"
    store = _store(path)
    record = store.upsert_event(_event("first"), guild_id=10, channel_id=20).record
    before = path.read_bytes()

    with pytest.raises(ValueError, match="succeeded"):
        GitHubHandledActionClaim(
            interaction_id=1,
            action=GitHubMirrorAction.REVIEW,
            task_id=str(uuid4()),
            succeeded=cast(bool, invalid),
        )

    assert path.read_bytes() == before
    assert b"prompt-modal-secret-value" not in before
    assert _store(path).get(record.mirror_id) == record


@dataclass
class _ThreadOutcome:
    value: object | None = None
    error: BaseException | None = None


def _run_bounded(operation: Callable[[], object]) -> _ThreadOutcome:
    outcome = _ThreadOutcome()

    def run() -> None:
        try:
            outcome.value = operation()
        except BaseException as error:
            outcome.error = error

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive(), "mirror store callback reentry deadlocked"
    return outcome


def test_compare_and_set_callback_can_read_same_store_without_deadlock(tmp_path: Path) -> None:
    store = _store(tmp_path / "reentrant-read.json")
    record = store.upsert_event(_event("first"), guild_id=10, channel_id=20).record

    outcome = _run_bounded(
        lambda: store.compare_and_set(
            record.mirror_id,
            record.revision,
            lambda current: replace(
                current,
                thread_id=store.get(current.mirror_id).channel_id,
            ),
        )
    )

    assert outcome.error is None
    updated = cast(GitHubMirrorRecord, outcome.value)
    assert updated.thread_id == record.channel_id
    assert updated.revision == record.revision + 1


def test_compare_and_set_callback_rejects_nested_mutation_without_overwrite(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "nested-mutation.json")
    record = store.upsert_event(_event("first"), guild_id=10, channel_id=20).record

    def nested_mutation(current: GitHubMirrorRecord) -> GitHubMirrorRecord:
        store.attach_card_if_missing(current.mirror_id, 99, "gm:" + "a" * 22)
        return current

    outcome = _run_bounded(
        lambda: store.compare_and_set(
            record.mirror_id,
            record.revision,
            nested_mutation,
        )
    )

    assert isinstance(outcome.error, RuntimeError)
    assert "callback" in str(outcome.error)
    assert store.get(record.mirror_id) == record


def test_compare_and_set_detects_nested_other_store_mutation_without_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested-other-store.json"
    first = _store(path)
    record = first.upsert_event(_event("first"), guild_id=10, channel_id=20).record
    claimed, claim_won = first.claim_card_creation(record.mirror_id)
    assert claim_won and claimed.card_create_nonce is not None
    creation_nonce = claimed.card_create_nonce
    second = _store(path)

    def nested_mutation(current: GitHubMirrorRecord) -> GitHubMirrorRecord:
        second.attach_card_if_missing(current.mirror_id, 99, creation_nonce)
        return replace(current, thread_id=303)

    outcome = _run_bounded(
        lambda: first.compare_and_set(
            claimed.mirror_id,
            claimed.revision,
            nested_mutation,
        )
    )

    assert isinstance(outcome.error, GitHubMirrorRevisionConflict)
    canonical = first.get(record.mirror_id)
    assert canonical.card_message_id == 99
    assert canonical.thread_id is None
    assert canonical.revision == claimed.revision + 1
