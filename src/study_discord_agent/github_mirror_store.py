import secrets
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
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
from study_discord_agent.github_mirror_store_types import (
    GitHubDeliveryCollision,
    GitHubMirrorMutationReentryError,
    GitHubMirrorRevisionConflict,
    GitHubMirrorStoreCorruptionError,
    GitHubMirrorUpsert,
)
from study_discord_agent.github_mirror_store_updates import (
    attach_card_record,
    claim_card_record,
    clear_card_record,
    complete_card_cleanup_record,
    queue_card_cleanup_record,
    record_from_event,
    release_card_creation_record,
    update_from_event,
    updated_record,
)
from study_discord_agent.posix_file_lock import PosixFileLock

DELIVERY_ID_LIMIT = MAX_RECENT_DELIVERIES
HANDLED_CLAIM_LIMIT = MAX_HANDLED_CLAIMS


def default_github_mirror_store_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "gateway" / "github-mirrors.json"


class _CallbackState(threading.local):
    def __init__(self) -> None:
        self.active = False
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

    def pending_publication_ids(self) -> tuple[str, ...]:
        with self._canonical_records() as records:
            return tuple(
                record.mirror_id
                for record in records.values()
                if record.publication_pending
                or record.card_message_id is None
                or record.card_create_pending
                or record.card_cleanup_nonce is not None
            )

    def get_by_item(
        self, repository: str, kind: GitHubItemKind, number: int
    ) -> GitHubMirrorRecord | None:
        with self._canonical_records() as records:
            key = (repository, kind, number)
            return next((record for record in records.values() if record.logical_key == key), None)

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
            self._commit_record(records, record)
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
            return self._commit_record(records, updated)

    def attach_card_if_missing(
        self, mirror_id: str, message_id: int, creation_nonce: str
    ) -> tuple[GitHubMirrorRecord, bool]:
        self._reject_callback_mutation()
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("message_id must be a positive integer")
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id is not None:
                if current.card_cleanup_nonce == creation_nonce:
                    return current, False
                if current.card_cleanup_nonce is not None:
                    raise GitHubMirrorRevisionConflict(mirror_id)
                updated = queue_card_cleanup_record(
                    current, creation_nonce, _timestamp(self._clock())
                )
                self._commit_record(records, updated)
                return updated, False
            if current.card_create_nonce != creation_nonce:
                raise GitHubMirrorRevisionConflict(mirror_id)
            updated = attach_card_record(
                current,
                message_id,
                creation_nonce,
                _timestamp(self._clock()),
            )
            self._commit_record(records, updated)
            return updated, True

    def claim_card_creation(self, mirror_id: str) -> tuple[GitHubMirrorRecord, bool]:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id is not None or current.card_create_pending:
                return current, False
            updated = claim_card_record(
                current,
                f"gm:{secrets.token_urlsafe(16)}",
                _timestamp(self._clock()),
            )
            self._commit_record(records, updated)
            return updated, True

    def release_card_creation(self, mirror_id: str, creation_nonce: str) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id is not None or current.card_create_nonce != creation_nonce:
                return current
            updated = release_card_creation_record(
                current,
                _timestamp(self._clock()),
            )
            return self._commit_record(records, updated)

    def complete_card_cleanup(self, mirror_id: str, cleanup_nonce: str) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_cleanup_nonce != cleanup_nonce:
                return current
            updated = complete_card_cleanup_record(current, _timestamp(self._clock()))
            return self._commit_record(records, updated)

    def complete_publication(
        self, mirror_id: str, expected_revision: int
    ) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.revision != expected_revision or not current.publication_pending:
                return current
            if (
                current.card_message_id is None
                or current.card_create_pending
                or current.card_cleanup_nonce is not None
            ):
                return current
            updated = updated_record(
                current,
                lambda record: replace(record, publication_pending=False),
                _timestamp(self._clock()),
            )
            return self._commit_record(records, updated)

    def clear_card_if_matches(self, mirror_id: str, message_id: int) -> GitHubMirrorRecord:
        self._reject_callback_mutation()
        with self._canonical_records() as records:
            current = records[mirror_id]
            if current.card_message_id != message_id:
                return current
            updated = clear_card_record(current, _timestamp(self._clock()))
            return self._commit_record(records, updated)

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

    def _commit_record(
        self,
        records: dict[str, GitHubMirrorRecord],
        record: GitHubMirrorRecord,
    ) -> GitHubMirrorRecord:
        updated_records = dict(records)
        updated_records[record.mirror_id] = record
        self._commit(updated_records)
        return record

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
