import pytest

from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAction,
    DiscordTaskAuthorizationError,
    authorize,
)
from study_discord_agent.discord_task_model import (
    DiscordTaskRecord,
    DiscordTaskSourceKind,
    DiscordTaskState,
)


def _record() -> DiscordTaskRecord:
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
        state=DiscordTaskState.RUNNING,
    )


def _access(
    *,
    actor_id: int = 1,
    guild_id: int = 2,
    channel_id: int = 4,
    visible_channel_ids: frozenset[int] = frozenset({3, 4}),
    manageable_channel_ids: frozenset[int] = frozenset(),
) -> DiscordTaskAccess:
    return DiscordTaskAccess(
        actor_id=actor_id,
        guild_id=guild_id,
        channel_id=channel_id,
        visible_channel_ids=visible_channel_ids,
        manageable_channel_ids=manageable_channel_ids,
    )


def test_scope_guards_apply_to_every_action() -> None:
    unrelated = _access(
        channel_id=99,
        visible_channel_ids=frozenset({3, 4, 99}),
    )
    for action in DiscordTaskAction:
        with pytest.raises(DiscordTaskAuthorizationError, match="guild"):
            authorize(_record(), action, _access(guild_id=99))
        for revoked_channel in (3, 4):
            visible = frozenset({3, 4} - {revoked_channel})
            with pytest.raises(DiscordTaskAuthorizationError, match="visible"):
                authorize(
                    _record(),
                    action,
                    _access(visible_channel_ids=visible),
                )
        with pytest.raises(DiscordTaskAuthorizationError, match="channel"):
            authorize(_record(), action, unrelated)


def test_owner_can_perform_every_action() -> None:
    for action in DiscordTaskAction:
        authorize(_record(), action, _access())


def test_visible_non_owner_can_only_view() -> None:
    access = _access(actor_id=99)

    authorize(_record(), DiscordTaskAction.VIEW, access)
    for action in (
        DiscordTaskAction.WHY_FAILED,
        DiscordTaskAction.STEER,
        DiscordTaskAction.STOP,
        DiscordTaskAction.RETRY,
        DiscordTaskAction.CONTINUE,
        DiscordTaskAction.FORGET,
    ):
        with pytest.raises(DiscordTaskAuthorizationError, match="owner"):
            authorize(_record(), action, access)


def test_moderator_can_stop_only_in_manageable_execution_channel() -> None:
    access = _access(actor_id=99, manageable_channel_ids=frozenset({4}))

    authorize(_record(), DiscordTaskAction.STOP, access)
    for action in (
        DiscordTaskAction.WHY_FAILED,
        DiscordTaskAction.STEER,
        DiscordTaskAction.RETRY,
        DiscordTaskAction.CONTINUE,
        DiscordTaskAction.FORGET,
    ):
        with pytest.raises(DiscordTaskAuthorizationError, match="owner"):
            authorize(_record(), action, access)


def test_moderator_cannot_stop_without_manageable_execution_channel() -> None:
    with pytest.raises(DiscordTaskAuthorizationError, match="owner"):
        authorize(_record(), DiscordTaskAction.STOP, _access(actor_id=99))
