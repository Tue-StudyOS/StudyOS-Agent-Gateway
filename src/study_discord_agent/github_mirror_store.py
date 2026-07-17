import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError, write_document
from study_discord_agent.github_mirror_model import (
    MAX_HANDLED_CLAIMS,
    MAX_RECENT_DELIVERIES,
    GitHubItemKind,
    GitHubMirrorEvent,
    GitHubMirrorRecord,
)
from study_discord_agent.github_mirror_serialization import decode_document, encode_document
from study_discord_agent.github_mirror_store_updates import (
    record_from_event,
    update_from_event,
    updated_record,
)
from study_discord_agent.posix_file_lock import PosixFileLock

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


class GitHubMirrorMutationReentryError(RuntimeError):
    pass


class _CallbackState(threading.local):
    def __init__(self) -> None:
        self.active = False


@dataclass(frozen=True)
class GitHubMirrorUpsert:
    record: GitHubMirrorRecord
    duplicate: bool


class GitHubMirrorStore:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._file_lock = PosixFileLock(path.with_name(f"{path.name}.lock"))
        self._callback_state = _CallbackState()
        self._records: dict[str, GitHubMirrorRecord] = {}
        with self._canonical_records():
            pass

    def get(self, mirror_id: str) -> GitHubMirrorRecord:
        with self._canonical_records() as records:
            return records[mirror_id]

    def records(self) -> tuple[GitHubMirrorRecord, ...]:
        with self._canonical_records() as records:
            return tuple(records.values())

    def get_by_item(
        self, repository: str, kind: GitHubItemKind, number: int
    ) -> GitHubMirrorRecord | None:
        with self._canonical_records() as records:
            key = (repository, kind, number)
            return next(
                (record for record in records.values() if record.logical_key == key), None
            )

    def upsert_event(
        self, event: GitHubMirrorEvent, *, guild_id: int, channel_id: int
    ) -> GitHubMirrorUpsert:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            logical_key = (
                event.repository_full_name,
                event.item_kind,
                event.item_number,
            )
            existing = next(
                (record for record in records.values() if record.logical_key == logical_key), None
            )
            delivery_owner = next(
                (
                    record
                    for record in records.values()
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
                record = record_from_event(event, guild_id, channel_id, timestamp)
            else:
                if (existing.guild_id, existing.channel_id) != (guild_id, channel_id):
                    raise ValueError("mirror destination cannot change during webhook upsert")
                record = update_from_event(existing, event, timestamp)
            updated_records = dict(records)
            updated_records[record.mirror_id] = record
            self._commit(updated_records)
            return GitHubMirrorUpsert(record, False)

    def compare_and_set(
        self,
        mirror_id: str,
        expected_revision: int,
        update: Callable[[GitHubMirrorRecord], GitHubMirrorRecord],
    ) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.revision != expected_revision:
                raise GitHubMirrorRevisionConflict(mirror_id)
        with self._compare_callback():
            candidate = update(current)
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.revision != expected_revision:
                raise GitHubMirrorRevisionConflict(mirror_id)
            updated = updated_record(
                current,
                lambda _: candidate,
                _timestamp(self._clock()),
            )
            updated_records = dict(records)
            updated_records[mirror_id] = updated
            self._commit(updated_records)
            return updated

    def attach_card_if_missing(
        self, mirror_id: str, message_id: int
    ) -> tuple[GitHubMirrorRecord, bool]:
        self._reject_callback_mutation()
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("message_id must be a positive integer")
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id is not None:
                return current, False
            updated = updated_record(
                current,
                lambda record: replace(record, card_message_id=message_id),
                _timestamp(self._clock()),
            )
            updated_records = dict(records)
            updated_records[mirror_id] = updated
            self._commit(updated_records)
            return updated, True

    def clear_card_if_matches(self, mirror_id: str, message_id: int) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id != message_id:
                return current
            updated = updated_record(
                current,
                lambda record: replace(record, card_message_id=None),
                _timestamp(self._clock()),
            )
            updated_records = dict(records)
            updated_records[mirror_id] = updated
            self._commit(updated_records)
            return updated

    @contextmanager
    def _compare_callback(self) -> Generator[None]:
        self._callback_state.active = True
        try:
            yield
        finally:
            self._callback_state.active = False

    def _reject_callback_mutation(self) -> None:
        if self._callback_state.active:
            raise GitHubMirrorMutationReentryError(
                "mirror store mutation is not allowed from a compare-and-set callback"
            )

    @contextmanager
    def _canonical_records(self) -> Generator[dict[str, GitHubMirrorRecord]]:
        with self._lock, self._file_lock.held():
            records = self._load()
            self._records = records
            yield records

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


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock timestamp must include a timezone")
    return value.astimezone(UTC).isoformat()
