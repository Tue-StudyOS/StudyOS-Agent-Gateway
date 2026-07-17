from datetime import UTC, datetime

from study_discord_agent.agent import AgentChannelCapabilities, AgentGateway
from study_discord_agent.discord_task_auth import (
    DiscordTaskAccess,
    DiscordTaskAction,
    authorize,
)
from study_discord_agent.discord_task_model import (
    ACTIVE_STATES,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_service_errors import (
    DiscordTaskActionUnavailable,
    DiscordTaskControlState,
)
from study_discord_agent.discord_task_store import DiscordTaskStore


class DiscordTaskQueries:
    def __init__(self, store: DiscordTaskStore, agent: AgentGateway) -> None:
        self._store = store
        self._agent = agent

    def status(self, task_id: str, access: DiscordTaskAccess) -> DiscordTaskRecord:
        record = self._store.get(task_id)
        authorize(record, DiscordTaskAction.VIEW, access)
        return record

    def active_task(self, execution_channel_id: int) -> DiscordTaskRecord | None:
        return next(
            (
                record
                for record in self._store.records()
                if record.execution_channel_id == execution_channel_id
                and record.state in ACTIVE_STATES
            ),
            None,
        )

    def list_tasks(
        self,
        access: DiscordTaskAccess,
        scope: str,
        state: str,
        current_channel_id: int,
    ) -> tuple[DiscordTaskRecord, ...]:
        if scope not in {"mine", "channel"} or state not in {
            "all",
            "active",
            "terminal",
        }:
            raise ValueError("task list scope or state is invalid")
        records = (
            record
            for record in self._store.records()
            if _visible(record, access)
            and _in_scope(record, access, scope, current_channel_id)
            and _in_state(record, state)
        )
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    datetime.fromisoformat(record.created_at).astimezone(UTC),
                    record.task_id,
                ),
                reverse=True,
            )[:10]
        )

    async def resolve_controls(
        self, task_id: str, access: DiscordTaskAccess
    ) -> DiscordTaskControlState:
        record = self.status(task_id, access)
        try:
            capabilities = await self._agent.channel_capabilities(
                record.execution_channel_id
            )
        except Exception:
            capabilities = AgentChannelCapabilities(False, False, False, False)
        record = self.status(task_id, access)
        return DiscordTaskControlState(
            steering=record.state is DiscordTaskState.RUNNING and capabilities.steering,
            resumable=(
                record.failure is not None
                and record.failure.retry_mode is DiscordTaskRetryMode.CONTINUE_SESSION
                and capabilities.resumable
            ),
            continuable=self.can_continue(record),
        )

    def can_continue(self, record: DiscordTaskRecord) -> bool:
        latest = max(
            (
                candidate
                for candidate in self._store.records()
                if candidate.execution_channel_id == record.execution_channel_id
            ),
            key=lambda candidate: (candidate.created_at, candidate.task_id),
            default=None,
        )
        return (
            latest is not None
            and latest.task_id == record.task_id
            and record.state is DiscordTaskState.COMPLETED
            and record.continued_to_task_id is None
        )

    def validate_continuation(
        self, parent: DiscordTaskRecord, request: DiscordTaskRequest
    ) -> None:
        if request.source_kind is not DiscordTaskSourceKind.CONTINUATION:
            raise DiscordTaskActionUnavailable("Continue requires a continuation request.")
        same_scope = (
            request.owner_id == parent.owner_id
            and request.guild_id == parent.guild_id
            and request.origin_channel_id == parent.origin_channel_id
            and request.execution_channel_id == parent.execution_channel_id
        )
        if not same_scope or not self.can_continue(parent):
            raise DiscordTaskActionUnavailable(
                "Only the latest completed task in this channel can continue."
            )


def _visible(record: DiscordTaskRecord, access: DiscordTaskAccess) -> bool:
    return record.guild_id == access.guild_id and {
        record.origin_channel_id,
        record.execution_channel_id,
    }.issubset(access.visible_channel_ids)


def _in_scope(
    record: DiscordTaskRecord,
    access: DiscordTaskAccess,
    scope: str,
    current_channel_id: int,
) -> bool:
    if scope == "mine":
        return record.owner_id == access.actor_id
    return (
        current_channel_id == access.channel_id
        and record.execution_channel_id == current_channel_id
    )


def _in_state(record: DiscordTaskRecord, state: str) -> bool:
    return (
        state == "all"
        or (state == "active" and record.state in ACTIVE_STATES)
        or (state == "terminal" and record.state not in ACTIVE_STATES)
    )
