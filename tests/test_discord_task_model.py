from dataclasses import replace

import pytest

from study_discord_agent.discord_task_model import (
    DiscordTaskInterruptionCause,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
    DiscordTaskState,
    InvalidDiscordTaskTransition,
    claim_interruption,
    transition,
)


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
    )


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
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
    ],
)
def test_transition_allows_documented_edges(
    from_state: DiscordTaskState, to_state: DiscordTaskState
) -> None:
    result = transition(_record(from_state), to_state, "2026-07-17T10:01:00+00:00")

    assert result.state is to_state
    assert result.updated_at == "2026-07-17T10:01:00+00:00"


@pytest.mark.parametrize("from_state", list(DiscordTaskState))
@pytest.mark.parametrize("to_state", list(DiscordTaskState))
def test_transition_rejects_every_undocumented_edge(
    from_state: DiscordTaskState, to_state: DiscordTaskState
) -> None:
    documented = {
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
    if (from_state, to_state) in documented:
        return

    with pytest.raises(InvalidDiscordTaskTransition):
        transition(_record(from_state), to_state, "2026-07-17T10:01:00+00:00")


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
