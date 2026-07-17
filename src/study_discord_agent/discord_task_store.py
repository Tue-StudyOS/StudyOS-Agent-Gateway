import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from study_discord_agent.discord_task_ids import (
    contains_task_id,
    lookup_task_key,
    validate_unique_task_ids,
)
from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskState,
    attempt_after_transition,
    can_transition,
    claim_interruption,
)
from study_discord_agent.discord_task_persistence import (
    TaskStoreDurabilityError,
    confirm_document_durability,
    write_document,
)
from study_discord_agent.discord_task_serialization import decode_document, encode_document
from study_discord_agent.discord_task_store_mutations import forget_inactive_task
from study_discord_agent.discord_task_store_policy import (
    apply_retention,
    same_task_scope,
    validate_continuation_graph,
)


class TaskStoreCorruptionError(RuntimeError):
    pass


class TaskRevisionConflict(RuntimeError):
    pass


class TaskAlreadyExists(RuntimeError):
    pass


class DiscordTaskStore:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._tasks = self._load()

    def create(self, record: DiscordTaskRecord) -> None:
        with self._lock:
            if contains_task_id(self._tasks, record.task_id):
                raise TaskAlreadyExists(record.task_id)
            if record.continued_from_task_id is not None or record.continued_to_task_id is not None:
                raise ValueError("continuation links may only be created by link_child")
            if record.attempt != 1:
                raise ValueError("new tasks must start at attempt one")
            self._ensure_execution_available(record, self._tasks)
            tasks = dict(self._tasks)
            tasks[record.task_id] = record
            self._commit(tasks)

    def get(self, task_id: str) -> DiscordTaskRecord:
        with self._lock:
            return self._tasks[lookup_task_key(self._tasks, task_id)]

    def records(self) -> tuple[DiscordTaskRecord, ...]:
        with self._lock:
            return tuple(self._tasks.values())

    def compare_and_set(
        self,
        task_id: str,
        expected_revision: int,
        update: Callable[[DiscordTaskRecord], DiscordTaskRecord],
    ) -> DiscordTaskRecord:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        with self._lock:
            task_key = lookup_task_key(self._tasks, task_id)
            current = self._tasks[task_key]
            if current.revision != expected_revision:
                raise TaskRevisionConflict(task_id)
            candidate = update(current)
            self._validate_update(current, candidate)
            self._ensure_execution_available(
                candidate,
                self._tasks,
                ignore_task_id=task_key,
            )
            updated = replace(candidate, revision=current.revision + 1)
            tasks = dict(self._tasks)
            tasks[task_key] = updated
            self._commit(tasks)
            return updated

    def link_child(
        self, parent_id: str, expected_revision: int, child: DiscordTaskRecord
    ) -> tuple[DiscordTaskRecord, DiscordTaskRecord]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        with self._lock:
            parent_key = lookup_task_key(self._tasks, parent_id)
            parent = self._tasks[parent_key]
            if parent.revision != expected_revision:
                raise TaskRevisionConflict(parent_id)
            if (
                parent.state is not DiscordTaskState.COMPLETED
                or parent.continued_to_task_id is not None
            ):
                raise ValueError("only an unlinked completed task can continue")
            if contains_task_id(self._tasks, child.task_id):
                raise TaskAlreadyExists(child.task_id)
            if child.attempt != 1:
                raise ValueError("continuation child must start at attempt one")
            if (
                child.state is not DiscordTaskState.STARTING
                or child.continued_from_task_id != parent.task_id
                or child.revision != 0
                or not same_task_scope(parent, child)
            ):
                raise ValueError("child must be a new linked starting task")
            self._ensure_execution_available(child, self._tasks)
            linked_parent = replace(
                parent,
                continued_to_task_id=child.task_id,
                revision=parent.revision + 1,
                updated_at=_timestamp(self._clock()),
            )
            tasks = dict(self._tasks)
            tasks[parent_key] = linked_parent
            tasks[child.task_id] = child
            self._commit(tasks)
            return linked_parent, child

    def reconcile_startup(self, now: datetime) -> tuple[DiscordTaskRecord, ...]:
        timestamp = _timestamp(now)
        changed: list[DiscordTaskRecord] = []
        with self._lock:
            tasks = dict(self._tasks)
            for task_id, record in self._tasks.items():
                reconciled = _reconcile(record, timestamp)
                if reconciled == record:
                    continue
                reconciled = replace(reconciled, revision=record.revision + 1)
                tasks[task_id] = reconciled
                changed.append(reconciled)
            if changed:
                self._commit(tasks)
        return tuple(changed)

    def forget(
        self, task_id: str, expected_revision: int
    ) -> tuple[DiscordTaskRecord, ...]:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        with self._lock:
            task_key = lookup_task_key(self._tasks, task_id)
            current = self._tasks[task_key]
            if current.revision != expected_revision:
                raise TaskRevisionConflict(task_id)
            tasks, neighbors = forget_inactive_task(
                self._tasks,
                task_key,
                updated_at=_timestamp(self._clock()),
            )
            self._commit(tasks)
            return neighbors

    def _load(self) -> dict[str, DiscordTaskRecord]:
        if not self._path.exists():
            return {}
        try:
            tasks = decode_document(self._path.read_text(encoding="utf-8"))
            validate_unique_task_ids(tasks)
            validate_continuation_graph(tasks)
            return tasks
        except OSError as error:
            raise TaskStoreCorruptionError("Discord task store is unreadable") from error
        except ValueError as error:
            raise TaskStoreCorruptionError(
                "Discord task store has an unsupported schema"
            ) from error

    def _commit(self, tasks: dict[str, DiscordTaskRecord]) -> None:
        retained = apply_retention(tasks, self._clock())
        try:
            self._write(retained)
        except TaskStoreDurabilityError:
            self._tasks = retained
            confirm_document_durability(self._path)
        else:
            self._tasks = retained

    def _write(self, tasks: Mapping[str, DiscordTaskRecord]) -> None:
        write_document(self._path, encode_document(tasks))

    @staticmethod
    def _ensure_execution_available(
        record: DiscordTaskRecord,
        tasks: Mapping[str, DiscordTaskRecord],
        *,
        ignore_task_id: str | None = None,
    ) -> None:
        if record.state not in ACTIVE_STATES:
            return
        for task in tasks.values():
            if task.task_id != ignore_task_id and (
                task.execution_channel_id == record.execution_channel_id
                and task.state in ACTIVE_STATES
            ):
                raise ValueError("an execution channel already has an active task")

    @staticmethod
    def _validate_update(current: DiscordTaskRecord, candidate: DiscordTaskRecord) -> None:
        immutable = (
            "task_id",
            "owner_id",
            "guild_id",
            "origin_channel_id",
            "execution_channel_id",
            "trigger_event_id",
            "source_message_id",
            "source_kind",
            "source_label",
            "created_at",
            "continued_from_task_id",
            "continued_to_task_id",
        )
        if any(getattr(current, field) != getattr(candidate, field) for field in immutable):
            raise ValueError("task identity fields cannot change")
        if candidate.revision != current.revision:
            raise ValueError("task revision cannot be supplied")
        if candidate.state != current.state and not can_transition(current.state, candidate.state):
            raise ValueError("task state transition is not allowed")
        if candidate.attempt != attempt_after_transition(current, candidate.state):
            raise ValueError("task attempt does not match its state transition")
        if (
            current.interruption_cause is not None
            and candidate.interruption_cause != current.interruption_cause
        ):
            raise ValueError("the first interruption cause is immutable")
        if (
            current.interruption_cause is None
            and candidate.interruption_cause is not None
            and current.state
            in {DiscordTaskState.DELIVERING, DiscordTaskState.COMPLETED}
        ):
            raise ValueError("delivery and completion cannot claim interruption")


def _reconcile(record: DiscordTaskRecord, timestamp: str) -> DiscordTaskRecord:
    if record.state in {
        DiscordTaskState.RECOVERING,
        DiscordTaskState.STARTING,
        DiscordTaskState.RUNNING,
        DiscordTaskState.STOPPING,
    }:
        return replace(
            claim_interruption(record, DiscordTaskInterruptionCause.GATEWAY_RESTART),
            state=DiscordTaskState.INTERRUPTED,
            updated_at=timestamp,
        )
    if record.state is DiscordTaskState.DELIVERING and record.result_message_id is not None:
        return replace(
            record, state=DiscordTaskState.COMPLETED, updated_at=timestamp, failure=None
        )
    if record.state is DiscordTaskState.DELIVERING:
        return replace(
            record,
            state=DiscordTaskState.DELIVERY_FAILED,
            updated_at=timestamp,
            failure=_delivery_retry_disabled(record.failure),
        )
    if (
        record.state is DiscordTaskState.DELIVERY_FAILED
        and record.failure is not None
        and record.failure.retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY
    ):
        return replace(
            record, updated_at=timestamp, failure=_delivery_retry_disabled(record.failure)
        )
    return record


def _delivery_retry_disabled(failure: DiscordTaskFailure | None) -> DiscordTaskFailure:
    if failure is None:
        return DiscordTaskFailure(
            category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
            summary="Discord could not deliver the result.",
            retry_mode=DiscordTaskRetryMode.NONE,
        )
    return replace(failure, retry_mode=DiscordTaskRetryMode.NONE)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return value.astimezone(UTC)
