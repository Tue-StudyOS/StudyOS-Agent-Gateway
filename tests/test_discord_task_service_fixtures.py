import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from study_discord_agent.agent import AgentExecutionContext, AgentGateway
from study_discord_agent.discord_delivery_cache import DiscordDeliveryCache
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_auth import DiscordTaskAccess
from study_discord_agent.discord_task_delivery import DiscordTaskPresentation
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskRetryMode,
    DiscordTaskSourceKind,
    DiscordTaskState,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.discord_task_service import DiscordTaskService
from study_discord_agent.discord_task_store import DiscordTaskStore
from tests.discord_task_service_fakes import FakeAgent, FakePresentation

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
ExecutionContextResolver = Callable[
    [DiscordTaskRecord], AgentExecutionContext | Awaitable[AgentExecutionContext]
]


class TrackingAttachments:
    def __init__(self, *paths: Path) -> None:
        self.paths = tuple(paths)
        self.directory = paths[0].parent if paths else None
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class TaskIdFactory:
    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> str:
        task_id = f"{self._next:032x}"
        self._next += 1
        return task_id


@dataclass
class ServiceHarness:
    service: DiscordTaskService
    store: DiscordTaskStore
    agent: FakeAgent
    presentation: FakePresentation
    cache: DiscordDeliveryCache


def make_harness(
    tmp_path: Path,
    *,
    agent: FakeAgent | None = None,
    presentation: FakePresentation | None = None,
    store: DiscordTaskStore | None = None,
    cache: DiscordDeliveryCache | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
    execution_context_resolver: ExecutionContextResolver | None = None,
) -> ServiceHarness:
    actual_agent = agent or FakeAgent()
    actual_presentation = presentation or FakePresentation()
    actual_store = store or DiscordTaskStore(tmp_path / "discord-tasks.json", clock=clock)
    actual_cache = cache or DiscordDeliveryCache()
    service = DiscordTaskService(
        agent=cast(AgentGateway, actual_agent),
        store=actual_store,
        presentation=cast(DiscordTaskPresentation, actual_presentation),
        delivery_cache=actual_cache,
        allowed_artifact_roots=(tmp_path,),
        max_artifact_bytes=8_000_000,
        clock=clock,
        task_id_factory=TaskIdFactory(),
        execution_context_resolver=execution_context_resolver,
    )
    return ServiceHarness(service, actual_store, actual_agent, actual_presentation, actual_cache)


def request(
    *,
    channel_id: int = 10,
    trigger_event_id: int = 100,
    owner_id: int = 1,
    prompt: str = "do the task",
    attachments: TrackingAttachments | None = None,
    source_kind: DiscordTaskSourceKind = DiscordTaskSourceKind.MENTION,
    intent: DiscordTaskIntent = DiscordTaskIntent.GENERAL,
    source_reference_id: str | None = None,
    repository_commit_sha: str | None = None,
    task_id: str | None = None,
) -> DiscordTaskRequest:
    staged = attachments or TrackingAttachments()
    return DiscordTaskRequest(
        source_kind=source_kind,
        guild_id=2,
        origin_channel_id=channel_id,
        execution_channel_id=channel_id,
        owner_id=owner_id,
        trigger_event_id=trigger_event_id,
        source_message_id=trigger_event_id,
        prompt=prompt,
        source_label="Discord request",
        attachments=cast(StagedDiscordAttachments, staged),
        origin_context=DiscordOriginContext(channel_id=channel_id),
        intent=intent,
        source_reference_id=source_reference_id,
        repository_commit_sha=repository_commit_sha,
        task_id=task_id,
    )


def access(
    *,
    channel_id: int = 10,
    actor_id: int = 1,
    visible: frozenset[int] | None = None,
) -> DiscordTaskAccess:
    return DiscordTaskAccess(
        actor_id=actor_id,
        guild_id=2,
        channel_id=channel_id,
        visible_channel_ids=visible or frozenset({channel_id}),
        manageable_channel_ids=frozenset(),
    )


def stored_record(
    task_id: str,
    state: DiscordTaskState,
    *,
    channel_id: int = 10,
    owner_id: int = 1,
    created_at: datetime = NOW,
    failure: DiscordTaskFailure | None = None,
    intent: DiscordTaskIntent = DiscordTaskIntent.GENERAL,
    source_reference_id: str | None = None,
    repository_commit_sha: str | None = None,
) -> DiscordTaskRecord:
    if failure is None and state in {DiscordTaskState.FAILED, DiscordTaskState.TIMED_OUT}:
        failure = DiscordTaskFailure(
            category=DiscordTaskFailureCategory.INTERNAL,
            summary="The task failed safely.",
            retry_mode=DiscordTaskRetryMode.NONE,
        )
    return DiscordTaskRecord(
        task_id=task_id,
        revision=0,
        owner_id=owner_id,
        guild_id=2,
        origin_channel_id=channel_id,
        execution_channel_id=channel_id,
        trigger_event_id=int(task_id, 16) + 100,
        source_message_id=None,
        card_message_id=None,
        result_message_id=None,
        source_kind=DiscordTaskSourceKind.MENTION,
        source_label="Stored task",
        created_at=created_at.isoformat(),
        updated_at=created_at.isoformat(),
        attempt=1,
        state=state,
        failure=failure,
        intent=intent,
        source_reference_id=source_reference_id,
        repository_commit_sha=repository_commit_sha,
    )


async def wait_for_state(
    store: DiscordTaskStore,
    task_id: str,
    state: DiscordTaskState,
) -> DiscordTaskRecord:
    for _ in range(200):
        record = store.get(task_id)
        if record.state is state:
            return record
        await asyncio.sleep(0.005)
    raise AssertionError(f"task {task_id} did not reach {state}; got {store.get(task_id).state}")


async def wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached")
