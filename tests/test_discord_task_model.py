from dataclasses import replace

import pytest

import study_discord_agent.discord_task_model as task_model
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskIntent,
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
    InvalidDiscordTaskTransition,
    claim_interruption,
    transition,
)

FAILURE = DiscordTaskFailure(
    category=DiscordTaskFailureCategory.INTERNAL,
    summary="The task failed safely.",
    retry_mode=DiscordTaskRetryMode.NONE,
)
DELIVERY_FAILURE = DiscordTaskFailure(
    category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
    summary="Discord could not deliver the result.",
    retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
)


def test_task_intents_cover_general_and_restricted_github_actions() -> None:
    assert {intent.value for intent in task_model.DiscordTaskIntent} == {
        "general",
        "review",
        "security_review",
        "vulnerability_scan",
        "implementation",
    }


def _record(state: DiscordTaskState = DiscordTaskState.STARTING) -> DiscordTaskRecord:
    return DiscordTaskRecord(
        task_id="123e4567-e89b-12d3-a456-426614174000",
        revision=0,
        owner_id=1,
        guild_id=2,
        origin_channel_id=3,
        execution_channel_id=4,
        trigger_event_id=5,
        source_message_id=None,
        card_message_id=None,
        result_message_id=None,
        source_kind=DiscordTaskSourceKind.MENTION,
        source_label="Discord mention",
        created_at="2026-07-17T10:00:00+00:00",
        updated_at="2026-07-17T10:00:00+00:00",
        attempt=1,
        state=state,
        failure=_failure_for(state),
    )


def _failure_for(state: DiscordTaskState) -> DiscordTaskFailure | None:
    if state is DiscordTaskState.DELIVERY_FAILED:
        return DELIVERY_FAILURE
    if state in {DiscordTaskState.FAILED, DiscordTaskState.TIMED_OUT}:
        return FAILURE
    return None


DOCUMENTED_EDGES = frozenset(
    {
        (DiscordTaskState.FAILED, DiscordTaskState.RECOVERING),
        (DiscordTaskState.TIMED_OUT, DiscordTaskState.RECOVERING),
        (DiscordTaskState.INTERRUPTED, DiscordTaskState.RECOVERING),
        (DiscordTaskState.RECOVERING, DiscordTaskState.STARTING),
        (DiscordTaskState.RECOVERING, DiscordTaskState.FAILED),
        (DiscordTaskState.RECOVERING, DiscordTaskState.STOPPING),
        (DiscordTaskState.STARTING, DiscordTaskState.RUNNING),
        (DiscordTaskState.STARTING, DiscordTaskState.FAILED),
        (DiscordTaskState.STARTING, DiscordTaskState.STOPPING),
        (DiscordTaskState.RUNNING, DiscordTaskState.DELIVERING),
        (DiscordTaskState.RUNNING, DiscordTaskState.FAILED),
        (DiscordTaskState.RUNNING, DiscordTaskState.TIMED_OUT),
        (DiscordTaskState.RUNNING, DiscordTaskState.STOPPING),
        (DiscordTaskState.RUNNING, DiscordTaskState.INTERRUPTED),
        (DiscordTaskState.STOPPING, DiscordTaskState.STOPPED),
        (DiscordTaskState.STOPPING, DiscordTaskState.FAILED),
        (DiscordTaskState.STOPPING, DiscordTaskState.INTERRUPTED),
        (DiscordTaskState.DELIVERING, DiscordTaskState.COMPLETED),
        (DiscordTaskState.DELIVERING, DiscordTaskState.DELIVERY_FAILED),
        (DiscordTaskState.DELIVERY_FAILED, DiscordTaskState.DELIVERING),
    }
)


def test_transition_graph_exactly_matches_documented_edges() -> None:
    timestamp = "2026-07-17T10:01:00+00:00"
    for from_state in DiscordTaskState:
        for to_state in DiscordTaskState:
            edge = (from_state, to_state)
            if edge in DOCUMENTED_EDGES:
                result = transition(
                    _record(from_state),
                    to_state,
                    timestamp,
                    failure=_failure_for(to_state),
                )
                assert result.state is to_state, edge
                assert result.updated_at == timestamp, edge
                continue
            with pytest.raises(InvalidDiscordTaskTransition):
                transition(
                    _record(from_state),
                    to_state,
                    timestamp,
                    failure=_failure_for(to_state),
                )


def test_interruption_claim_preserves_the_first_cause_until_delivery() -> None:
    claimed = claim_interruption(
        _record(DiscordTaskState.RUNNING), DiscordTaskInterruptionCause.TIMEOUT
    )
    later = claim_interruption(claimed, DiscordTaskInterruptionCause.USER_STOP)

    assert claimed.interruption_cause is DiscordTaskInterruptionCause.TIMEOUT
    assert later == claimed
    assert (
        claim_interruption(
            _record(DiscordTaskState.DELIVERING), DiscordTaskInterruptionCause.GATEWAY_RESTART
        ).interruption_cause
        is None
    )


def test_delivery_retry_and_stop_clear_stale_failure_metadata() -> None:
    delivering = transition(
        _record(DiscordTaskState.DELIVERY_FAILED),
        DiscordTaskState.DELIVERING,
        "2026-07-17T10:01:00+00:00",
    )
    completed = transition(delivering, DiscordTaskState.COMPLETED, "2026-07-17T10:02:00+00:00")
    recovering = transition(
        _record(DiscordTaskState.FAILED),
        DiscordTaskState.RECOVERING,
        "2026-07-17T10:01:00+00:00",
    )
    stopped = transition(
        transition(recovering, DiscordTaskState.STOPPING, "2026-07-17T10:02:00+00:00"),
        DiscordTaskState.STOPPED,
        "2026-07-17T10:03:00+00:00",
    )

    assert completed.failure is None
    assert recovering.failure is FAILURE
    assert stopped.failure is None


def test_record_rejects_unsafe_or_invalid_persisted_values() -> None:
    with pytest.raises(ValueError, match="source_label"):
        replace(_record(), source_label="")
    with pytest.raises(ValueError, match="UUID"):
        replace(_record(), task_id="not-a-uuid")
    with pytest.raises(ValueError, match="attempt"):
        replace(_record(), attempt=0)
    with pytest.raises(ValueError, match="timezone"):
        replace(_record(), updated_at="2026-07-17T10:00:00")
    with pytest.raises(ValueError, match="continue itself"):
        replace(_record(), continued_from_task_id=_record().task_id)


def test_record_validates_persisted_task_bridge_metadata() -> None:
    record = replace(
        _record(),
        intent=DiscordTaskIntent.SECURITY_REVIEW,
        source_reference_id="a" * 32,
        repository_commit_sha="b" * 40,
    )

    assert record.intent is DiscordTaskIntent.SECURITY_REVIEW
    assert record.source_reference_id == "a" * 32
    assert record.repository_commit_sha == "b" * 40
    invalid = (
        ({"intent": "review"}, "intent"),
        ({"source_reference_id": "A" * 32}, "source_reference_id"),
        ({"repository_commit_sha": "b" * 39}, "repository_commit_sha"),
    )
    for changes, message in invalid:
        with pytest.raises(ValueError, match=message):
            replace(_record(), **changes)
