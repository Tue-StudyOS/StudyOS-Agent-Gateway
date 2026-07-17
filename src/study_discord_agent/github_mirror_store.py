import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError, write_document
from study_discord_agent.github_mirror_model import (
    MAX_HANDLED_CLAIMS,
    MAX_RECENT_DELIVERIES,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
    GitHubMirrorRecord,
)
from study_discord_agent.github_mirror_serialization import decode_document, encode_document

DELIVERY_ID_LIMIT = MAX_RECENT_DELIVERIES
HANDLED_CLAIM_LIMIT = MAX_HANDLED_CLAIMS


def default_github_mirror_store_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "gateway" / "github-mirrors.json"


class GitHubMirrorStoreCorruptionError(RuntimeError):
    pass


class GitHubDeliveryCollision(RuntimeError):
    pass


class GitHubMirrorRevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubMirrorUpsert:
    record: GitHubMirrorRecord
    duplicate: bool


class GitHubMirrorStore:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._records = self._load()

    def get(self, mirror_id: str) -> GitHubMirrorRecord:
        with self._lock:
            return self._records[mirror_id]

    def records(self) -> tuple[GitHubMirrorRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def get_by_item(
        self, repository: str, kind: GitHubItemKind, number: int
    ) -> GitHubMirrorRecord | None:
        with self._lock:
            key = (repository, kind, number)
            return next(
                (record for record in self._records.values() if record.logical_key == key), None
            )

    def upsert_event(
        self, event: GitHubMirrorEvent, *, guild_id: int, channel_id: int
    ) -> GitHubMirrorUpsert:
        with self._lock:
            existing = self.get_by_item(
                event.repository_full_name, event.item_kind, event.item_number
            )
            delivery_owner = next(
                (
                    record
                    for record in self._records.values()
                    if event.delivery_id in record.recent_delivery_ids
                ),
                None,
            )
            if delivery_owner is not None:
                if existing is not None and delivery_owner.mirror_id == existing.mirror_id:
                    return GitHubMirrorUpsert(existing, True)
                raise GitHubDeliveryCollision(event.delivery_id)
            timestamp = _timestamp(self._clock())
            if existing is None:
                record = _record_from_event(event, guild_id, channel_id, timestamp)
            else:
                if (existing.guild_id, existing.channel_id) != (guild_id, channel_id):
                    raise ValueError("mirror destination cannot change during webhook upsert")
                record = _update_from_event(existing, event, timestamp)
            records = dict(self._records)
            records[record.mirror_id] = record
            self._commit(records)
            return GitHubMirrorUpsert(record, False)

    def compare_and_set(
        self,
        mirror_id: str,
        expected_revision: int,
        update: Callable[[GitHubMirrorRecord], GitHubMirrorRecord],
    ) -> GitHubMirrorRecord:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        with self._lock:
            current = self._records[mirror_id]
            if current.revision != expected_revision:
                raise GitHubMirrorRevisionConflict(mirror_id)
            candidate = update(current)
            _validate_update(current, candidate)
            updated = replace(
                candidate,
                revision=current.revision + 1,
                handled_interaction_claims=candidate.handled_interaction_claims[
                    -HANDLED_CLAIM_LIMIT:
                ],
                updated_at=_timestamp(self._clock()),
            )
            records = dict(self._records)
            records[mirror_id] = updated
            self._commit(records)
            return updated

    def attach_card_if_missing(
        self, mirror_id: str, message_id: int
    ) -> tuple[GitHubMirrorRecord, bool]:
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("message_id must be a positive integer")
        with self._lock:
            current = self._records[mirror_id]
            if current.card_message_id is not None:
                return current, False
            return (
                self.compare_and_set(
                    mirror_id,
                    current.revision,
                    lambda record: replace(record, card_message_id=message_id),
                ),
                True,
            )

    def clear_card_if_matches(self, mirror_id: str, message_id: int) -> GitHubMirrorRecord:
        with self._lock:
            current = self._records[mirror_id]
            if current.card_message_id != message_id:
                return current
            return self.compare_and_set(
                mirror_id,
                current.revision,
                lambda record: replace(record, card_message_id=None),
            )

    def _load(self) -> dict[str, GitHubMirrorRecord]:
        if not self._path.exists():
            return {}
        try:
            return decode_document(self._path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, KeyError) as error:
            raise GitHubMirrorStoreCorruptionError(
                "GitHub mirror store has an unsupported or corrupt schema"
            ) from error

    def _commit(self, records: dict[str, GitHubMirrorRecord]) -> None:
        try:
            write_document(self._path, encode_document(records))
        except TaskStoreDurabilityError:
            self._records = records
            raise
        else:
            self._records = records


def _record_from_event(
    event: GitHubMirrorEvent, guild_id: int, channel_id: int, timestamp: str
) -> GitHubMirrorRecord:
    return GitHubMirrorRecord(
        mirror_id=uuid4().hex,
        revision=0,
        guild_id=guild_id,
        channel_id=channel_id,
        card_message_id=None,
        thread_id=None,
        repository_full_name=event.repository_full_name,
        item_kind=event.item_kind,
        item_number=event.item_number,
        item_url=event.item_url,
        title=event.title,
        state=event.state,
        author_login=event.author_login,
        labels=event.labels,
        base_ref=event.base_ref,
        head_ref=event.head_ref,
        base_sha=event.base_sha,
        head_sha=event.head_sha,
        activity=event.activity,
        item_updated_at=event.item_updated_at,
        recent_delivery_ids=(event.delivery_id,),
        pending_action=None,
        handled_interaction_claims=(),
        active_task_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _update_from_event(
    record: GitHubMirrorRecord, event: GitHubMirrorEvent, timestamp: str
) -> GitHubMirrorRecord:
    deliveries = (*record.recent_delivery_ids, event.delivery_id)[-DELIVERY_ID_LIMIT:]
    changes: dict[str, object] = {
        "recent_delivery_ids": deliveries,
        "revision": record.revision + 1,
        "updated_at": timestamp,
    }
    if _parse_timestamp(event.item_updated_at) >= _parse_timestamp(record.item_updated_at):
        state = event.state
        if event.event_name == "issue_comment" and event.item_kind is GitHubItemKind.PULL_REQUEST:
            if record.state is GitHubItemState.MERGED:
                state = GitHubItemState.MERGED
            elif record.state is GitHubItemState.DRAFT and event.state is GitHubItemState.OPEN:
                state = GitHubItemState.DRAFT
        changes.update(
            title=event.title,
            state=state,
            author_login=event.author_login,
            labels=event.labels,
            base_ref=event.base_ref if event.base_ref is not None else record.base_ref,
            head_ref=event.head_ref if event.head_ref is not None else record.head_ref,
            base_sha=event.base_sha if event.base_sha is not None else record.base_sha,
            head_sha=event.head_sha if event.head_sha is not None else record.head_sha,
            activity=event.activity,
            item_updated_at=event.item_updated_at,
        )
    return replace(record, **changes)


def _validate_update(current: GitHubMirrorRecord, candidate: GitHubMirrorRecord) -> None:
    immutable = (
        "mirror_id",
        "revision",
        "guild_id",
        "channel_id",
        "repository_full_name",
        "item_kind",
        "item_number",
        "item_url",
        "title",
        "state",
        "author_login",
        "labels",
        "base_ref",
        "head_ref",
        "base_sha",
        "head_sha",
        "activity",
        "item_updated_at",
        "created_at",
        "recent_delivery_ids",
    )
    if any(getattr(current, field) != getattr(candidate, field) for field in immutable):
        raise ValueError("mirror identity and delivery fields cannot change through CAS")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock timestamp must include a timezone")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
