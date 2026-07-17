from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from study_discord_agent.github_mirror_model import (
    GitHubHandledActionClaim,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorRecord,
    GitHubPendingAction,
)
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.github_mirror_store_types import GitHubMirrorRevisionConflict

_CAS_ATTEMPTS = 8


class GitHubMirrorActionUnavailable(RuntimeError):
    pass


class GitHubMirrorActionBusy(GitHubMirrorActionUnavailable):
    def __init__(self, task_id: str) -> None:
        super().__init__("This GitHub item already has an active StudyOS task.")
        self.task_id = task_id


@dataclass(frozen=True)
class GitHubActionReservation:
    record: GitHubMirrorRecord
    task_id: str
    accepted: bool
    succeeded: bool | None


class GitHubMirrorActionStore:
    def __init__(
        self,
        store: GitHubMirrorStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def reserve(
        self,
        mirror_id: str,
        interaction_id: int,
        action: GitHubMirrorAction,
        task_id: str,
    ) -> GitHubActionReservation:
        _canonical_uuid(task_id)
        for _ in range(_CAS_ATTEMPTS):
            current = self._store.get(mirror_id)
            prior = _reservation_for(current, interaction_id)
            if prior is not None:
                return prior
            if current.pending_action is not None:
                raise GitHubMirrorActionBusy(current.pending_action.task_id)
            if current.active_task_id is not None:
                raise GitHubMirrorActionBusy(current.active_task_id)
            if current.state not in {GitHubItemState.OPEN, GitHubItemState.DRAFT}:
                raise GitHubMirrorActionUnavailable("This GitHub item is no longer open.")
            pending = GitHubPendingAction(
                interaction_id=interaction_id,
                action=action,
                task_id=task_id,
                claimed_at=_timestamp(self._clock()),
            )
            try:
                updated = self._store.compare_and_set(
                    mirror_id,
                    current.revision,
                    lambda record, claim=pending: replace(
                        record,
                        pending_action=claim,
                    ),
                )
            except GitHubMirrorRevisionConflict:
                continue
            return GitHubActionReservation(updated, task_id, True, None)
        raise GitHubMirrorActionUnavailable("The GitHub card changed. Try again.")

    def attach_thread(self, mirror_id: str, task_id: str, thread_id: int) -> GitHubMirrorRecord:
        if type(thread_id) is not int or thread_id <= 0:
            raise ValueError("thread_id must be a positive integer")
        return self._update_pending(
            mirror_id,
            task_id,
            lambda record: replace(record, thread_id=thread_id),
            allow_existing_thread=thread_id,
        )

    def finish(self, mirror_id: str, task_id: str, *, succeeded: bool) -> GitHubMirrorRecord:
        def complete(record: GitHubMirrorRecord) -> GitHubMirrorRecord:
            pending = _matching_pending(record, task_id)
            claim = GitHubHandledActionClaim(
                interaction_id=pending.interaction_id,
                action=pending.action,
                task_id=task_id,
                succeeded=succeeded,
            )
            return replace(
                record,
                pending_action=None,
                active_task_id=task_id if succeeded else None,
                handled_interaction_claims=(*record.handled_interaction_claims, claim),
            )

        return self._update_pending(mirror_id, task_id, complete)

    def clear_active(self, mirror_id: str, task_id: str) -> GitHubMirrorRecord:
        for _ in range(_CAS_ATTEMPTS):
            current = self._store.get(mirror_id)
            if current.active_task_id != task_id:
                return current
            try:
                return self._store.compare_and_set(
                    mirror_id,
                    current.revision,
                    lambda record: replace(record, active_task_id=None),
                )
            except GitHubMirrorRevisionConflict:
                continue
        raise GitHubMirrorActionUnavailable("The GitHub card changed. Try again.")

    def _update_pending(
        self,
        mirror_id: str,
        task_id: str,
        update: Callable[[GitHubMirrorRecord], GitHubMirrorRecord],
        *,
        allow_existing_thread: int | None = None,
    ) -> GitHubMirrorRecord:
        for _ in range(_CAS_ATTEMPTS):
            current = self._store.get(mirror_id)
            _matching_pending(current, task_id)
            if (
                allow_existing_thread is not None
                and current.thread_id is not None
                and current.thread_id != allow_existing_thread
            ):
                raise GitHubMirrorActionUnavailable(
                    "This GitHub item is already bound to another thread."
                )
            try:
                return self._store.compare_and_set(
                    mirror_id,
                    current.revision,
                    update,
                )
            except GitHubMirrorRevisionConflict:
                continue
        raise GitHubMirrorActionUnavailable("The GitHub card changed. Try again.")


def _reservation_for(
    record: GitHubMirrorRecord,
    interaction_id: int,
) -> GitHubActionReservation | None:
    pending = record.pending_action
    if pending is not None and pending.interaction_id == interaction_id:
        return GitHubActionReservation(record, pending.task_id, False, None)
    claim = next(
        (
            candidate
            for candidate in record.handled_interaction_claims
            if candidate.interaction_id == interaction_id
        ),
        None,
    )
    if claim is None:
        return None
    return GitHubActionReservation(record, claim.task_id, False, claim.succeeded)


def _matching_pending(record: GitHubMirrorRecord, task_id: str) -> GitHubPendingAction:
    pending = record.pending_action
    if pending is None or pending.task_id != task_id:
        raise GitHubMirrorActionUnavailable("This GitHub action is no longer pending.")
    return pending


def _canonical_uuid(value: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("task_id must be a canonical UUID") from error
    if str(parsed) != value:
        raise ValueError("task_id must be a canonical UUID")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock timestamp must include a timezone")
    return value.astimezone(UTC).isoformat()
