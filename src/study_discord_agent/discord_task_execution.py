from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from inspect import isawaitable

from study_discord_agent.agent import AgentExecutionContext
from study_discord_agent.agent_errors import AgentConfigurationError
from study_discord_agent.agent_execution_policy import AgentPolicyClass
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import (
    DiscordTaskIntent,
    DiscordTaskRecord,
    DiscordTaskSourceKind,
)
from study_discord_agent.discord_task_request import DiscordTaskRequest

type DiscordTaskExecutionContextResolver = Callable[
    [DiscordTaskRecord], AgentExecutionContext | Awaitable[AgentExecutionContext]
]


@dataclass(frozen=True)
class AgentRunSpec:
    prompt: str
    source_message_id: int | None
    attachments: StagedDiscordAttachments
    origin_context: DiscordOriginContext | None
    recovering: bool = False
    create_card: bool = False
    require_existing_session: bool = False

    @classmethod
    def from_request(cls, request: DiscordTaskRequest) -> "AgentRunSpec":
        return cls(
            prompt=request.prompt,
            source_message_id=request.source_message_id,
            attachments=request.attachments,
            origin_context=request.origin_context,
            create_card=True,
            require_existing_session=(request.source_kind is DiscordTaskSourceKind.CONTINUATION),
        )

    @classmethod
    def for_recovery(cls, prompt: str) -> "AgentRunSpec":
        return cls(
            prompt=prompt,
            source_message_id=None,
            attachments=StagedDiscordAttachments(paths=(), directory=None),
            origin_context=None,
            recovering=True,
            require_existing_session=True,
        )


def default_execution_context(record: DiscordTaskRecord) -> AgentExecutionContext:
    if (
        record.intent is not DiscordTaskIntent.GENERAL
        or record.source_reference_id is not None
        or record.repository_commit_sha is not None
    ):
        raise AgentConfigurationError(
            "A task execution context resolver is required for repository tasks"
        )
    return AgentExecutionContext(
        channel_id=record.execution_channel_id,
        trigger_event_id=record.trigger_event_id,
    )


async def resolve_execution_context(
    resolver: DiscordTaskExecutionContextResolver,
    record: DiscordTaskRecord,
    *,
    require_existing_session: bool,
) -> AgentExecutionContext:
    resolved = resolver(record)
    context = await resolved if isawaitable(resolved) else resolved
    _validate_context(record, context)
    return replace(context, require_existing_session=require_existing_session)


def _validate_context(
    record: DiscordTaskRecord,
    context: AgentExecutionContext,
) -> None:
    if (
        context.channel_id != record.execution_channel_id
        or context.trigger_event_id != record.trigger_event_id
    ):
        raise AgentConfigurationError("Resolved task identity does not match the task record")
    if context.repository_commit_sha != record.repository_commit_sha:
        raise AgentConfigurationError("Resolved commit does not match the task record")
    policy = context.execution_policy
    if record.intent is DiscordTaskIntent.GENERAL:
        if policy is not None:
            raise AgentConfigurationError("General tasks cannot use a restricted task policy")
        return
    expected_policy = AgentPolicyClass(record.intent.value)
    if policy is None or policy.policy_class is not expected_policy:
        raise AgentConfigurationError("Resolved policy does not match the task intent")
