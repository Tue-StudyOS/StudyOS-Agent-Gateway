from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID


class DiscordTaskState(StrEnum):
    STARTING = "starting"
    RECOVERING = "recovering"
    RUNNING = "running"
    STOPPING = "stopping"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    DELIVERY_FAILED = "delivery_failed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"


class DiscordTaskSourceKind(StrEnum):
    MENTION = "mention"
    SLASH = "slash"
    CONTEXT_ACTION = "context_action"
    CONTINUATION = "continuation"


class DiscordTaskInterruptionCause(StrEnum):
    USER_STOP = "user_stop"
    TIMEOUT = "timeout"
    RUNTIME_EXIT = "runtime_exit"
    GATEWAY_RESTART = "gateway_restart"


class DiscordTaskFailureCategory(StrEnum):
    TIMEOUT = "timeout"
    RUNTIME_DISCONNECTED = "runtime_disconnected"
    AGENT_PROCESS_FAILED = "agent_process_failed"
    INVALID_AGENT_OUTPUT = "invalid_agent_output"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    CONFIGURATION = "configuration"
    WORKSPACE_OR_ATTACHMENT = "workspace_or_attachment"
    DISCORD_DELIVERY = "discord_delivery"
    INTERNAL = "internal"


class DiscordTaskRetryMode(StrEnum):
    CONTINUE_SESSION = "continue_session"
    RETRY_DELIVERY = "retry_delivery"
    NONE = "none"


@dataclass(frozen=True)
class DiscordTaskFailure:
    category: DiscordTaskFailureCategory
    summary: str
    retry_mode: DiscordTaskRetryMode

    def __post_init__(self) -> None:
        if not self.summary or len(self.summary) > 500:
            raise ValueError("failure summary must be between 1 and 500 characters")


@dataclass(frozen=True)
class DiscordTaskRecord:
    task_id: str
    revision: int
    owner_id: int
    guild_id: int
    origin_channel_id: int
    execution_channel_id: int
    trigger_event_id: int
    source_message_id: int | None
    card_message_id: int | None
    result_message_id: int | None
    source_kind: DiscordTaskSourceKind
    source_label: str
    created_at: str
    updated_at: str
    attempt: int
    state: DiscordTaskState
    failure: DiscordTaskFailure | None = None
    interruption_cause: DiscordTaskInterruptionCause | None = None
    continued_from_task_id: str | None = None
    continued_to_task_id: str | None = None

    def __post_init__(self) -> None:
        _validate_uuid(self.task_id, "task_id")
        _validate_uuid(self.continued_from_task_id, "continued_from_task_id")
        _validate_uuid(self.continued_to_task_id, "continued_to_task_id")
        if self.task_id in {self.continued_from_task_id, self.continued_to_task_id}:
            raise ValueError("a task cannot continue itself")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be non-negative")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be at least one")
        if not self.source_label or len(self.source_label) > 200:
            raise ValueError("source_label must be between 1 and 200 characters")
        for name, value in (
            ("owner_id", self.owner_id),
            ("guild_id", self.guild_id),
            ("origin_channel_id", self.origin_channel_id),
            ("execution_channel_id", self.execution_channel_id),
            ("trigger_event_id", self.trigger_event_id),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (
            ("source_message_id", self.source_message_id),
            ("card_message_id", self.card_message_id),
            ("result_message_id", self.result_message_id),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer or None")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.updated_at, "updated_at")
        if self.state in _FAILURE_STATES and self.failure is None:
            raise ValueError(f"{self.state} requires a failure")
        if self.state in {DiscordTaskState.COMPLETED, DiscordTaskState.STOPPED} and self.failure:
            raise ValueError(f"{self.state} cannot carry failure metadata")
        if self.state is DiscordTaskState.DELIVERY_FAILED:
            _validate_delivery_failure(self.failure)
        if (
            self.failure is not None
            and (
                self.failure.category is DiscordTaskFailureCategory.DISCORD_DELIVERY
                or self.failure.retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY
            )
            and self.state is not DiscordTaskState.DELIVERY_FAILED
        ):
            raise ValueError("delivery retry metadata is exclusive to delivery failure")


ACTIVE_STATES: Final[frozenset[DiscordTaskState]] = frozenset(
    {
        DiscordTaskState.RECOVERING,
        DiscordTaskState.STARTING,
        DiscordTaskState.RUNNING,
        DiscordTaskState.STOPPING,
        DiscordTaskState.DELIVERING,
    }
)
_FAILURE_STATES: Final[frozenset[DiscordTaskState]] = frozenset(
    {
        DiscordTaskState.FAILED,
        DiscordTaskState.TIMED_OUT,
        DiscordTaskState.DELIVERY_FAILED,
    }
)
_TRANSITIONS: Final[dict[DiscordTaskState, frozenset[DiscordTaskState]]] = {
    DiscordTaskState.FAILED: frozenset({DiscordTaskState.RECOVERING}),
    DiscordTaskState.TIMED_OUT: frozenset({DiscordTaskState.RECOVERING}),
    DiscordTaskState.INTERRUPTED: frozenset({DiscordTaskState.RECOVERING}),
    DiscordTaskState.RECOVERING: frozenset(
        {DiscordTaskState.STARTING, DiscordTaskState.FAILED, DiscordTaskState.STOPPING}
    ),
    DiscordTaskState.STARTING: frozenset(
        {DiscordTaskState.RUNNING, DiscordTaskState.FAILED, DiscordTaskState.STOPPING}
    ),
    DiscordTaskState.RUNNING: frozenset(
        {
            DiscordTaskState.DELIVERING,
            DiscordTaskState.FAILED,
            DiscordTaskState.TIMED_OUT,
            DiscordTaskState.STOPPING,
            DiscordTaskState.INTERRUPTED,
        }
    ),
    DiscordTaskState.STOPPING: frozenset(
        {DiscordTaskState.STOPPED, DiscordTaskState.FAILED, DiscordTaskState.INTERRUPTED}
    ),
    DiscordTaskState.DELIVERING: frozenset(
        {DiscordTaskState.COMPLETED, DiscordTaskState.DELIVERY_FAILED}
    ),
    DiscordTaskState.DELIVERY_FAILED: frozenset({DiscordTaskState.DELIVERING}),
    DiscordTaskState.COMPLETED: frozenset(),
    DiscordTaskState.STOPPED: frozenset(),
}


class InvalidDiscordTaskTransition(ValueError):
    pass


def can_transition(current: DiscordTaskState, target: DiscordTaskState) -> bool:
    return target in _TRANSITIONS[current]


def transition(
    record: DiscordTaskRecord,
    target: DiscordTaskState,
    updated_at: str,
    *,
    failure: DiscordTaskFailure | None = None,
) -> DiscordTaskRecord:
    if not can_transition(record.state, target):
        raise InvalidDiscordTaskTransition(f"cannot transition {record.state} to {target}")
    if target in {DiscordTaskState.COMPLETED, DiscordTaskState.STOPPED} or (
        record.state is DiscordTaskState.DELIVERY_FAILED and target is DiscordTaskState.DELIVERING
    ):
        next_failure = None
    else:
        next_failure = failure or record.failure
    return replace(
        record,
        state=target,
        updated_at=updated_at,
        failure=next_failure,
        attempt=attempt_after_transition(record, target),
    )


def attempt_after_transition(record: DiscordTaskRecord, target: DiscordTaskState) -> int:
    retrying_agent = target is DiscordTaskState.RECOVERING and record.state in {
        DiscordTaskState.FAILED,
        DiscordTaskState.TIMED_OUT,
        DiscordTaskState.INTERRUPTED,
    }
    retrying_delivery = (
        record.state is DiscordTaskState.DELIVERY_FAILED and target is DiscordTaskState.DELIVERING
    )
    return record.attempt + 1 if retrying_agent or retrying_delivery else record.attempt


def claim_interruption(
    record: DiscordTaskRecord, cause: DiscordTaskInterruptionCause
) -> DiscordTaskRecord:
    if record.state in {DiscordTaskState.DELIVERING, DiscordTaskState.COMPLETED}:
        return record
    if record.interruption_cause is not None:
        return record
    return replace(record, interruption_cause=cause)


def _validate_uuid(value: str | None, name: str) -> None:
    if value is None:
        return
    try:
        UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a UUID") from error


def _validate_timestamp(value: str, name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must have a timezone")


def _validate_delivery_failure(failure: DiscordTaskFailure | None) -> None:
    if failure is None:
        raise ValueError("delivery failure requires a failure")
    if failure.category is not DiscordTaskFailureCategory.DISCORD_DELIVERY:
        raise ValueError("delivery failure must be categorized as Discord delivery")
    if failure.retry_mode not in {
        DiscordTaskRetryMode.RETRY_DELIVERY,
        DiscordTaskRetryMode.NONE,
    }:
        raise ValueError("delivery failure cannot continue the agent session")
