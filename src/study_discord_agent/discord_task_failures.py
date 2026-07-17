from typing import Final

from study_discord_agent.agent_errors import (
    AgentConfigurationError,
    AgentInvalidOutput,
    AgentProcessFailed,
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
    AgentTurnTimedOut,
    AgentWorkspaceOrAttachmentError,
)
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRetryMode,
)

_TIMEOUT_SUMMARY: Final = "The agent timed out. Partial work and the agent session were kept."
_DISCONNECTED_SUMMARY: Final = "Codex app server disconnected. The session and worktree were kept."
_NO_SESSION_SUMMARY: Final = "The agent session was not saved, so recovery is unavailable."
_ACTIVE_TURN_SUMMARY: Final = "An agent turn may still be active, so recovery is unavailable yet."
_INCOMPATIBLE_SUMMARY: Final = "The configured Codex app server is incompatible."
_PROCESS_SUMMARY: Final = "The agent process exited before returning a result."
_INVALID_OUTPUT_SUMMARY: Final = "The agent returned an invalid result."
_CONFIGURATION_SUMMARY: Final = "The gateway configuration is incomplete."
_WORKSPACE_SUMMARY: Final = "The workspace or attachment could not be prepared safely."
_INTERNAL_SUMMARY: Final = "The task failed safely. Check the task ID with an administrator."
_RETRY_DELIVERY_SUMMARY: Final = (
    "Discord could not deliver the result. It is safe to retry delivery."
)
_AMBIGUOUS_DELIVERY_SUMMARY: Final = (
    "Discord delivery may have succeeded. Check the channel before trying again."
)


def classify_agent_failure(
    error: BaseException, *, persisted_session: bool, active_turn: bool
) -> DiscordTaskFailure:
    if isinstance(error, AgentTurnTimedOut):
        return _resumable_failure(
            DiscordTaskFailureCategory.TIMEOUT,
            _TIMEOUT_SUMMARY,
            persisted_session=persisted_session,
            active_turn=active_turn,
        )
    if isinstance(error, AgentRuntimeDisconnected):
        return _resumable_failure(
            DiscordTaskFailureCategory.RUNTIME_DISCONNECTED,
            _DISCONNECTED_SUMMARY,
            persisted_session=persisted_session,
            active_turn=active_turn,
        )
    if isinstance(error, AgentRuntimeIncompatible):
        return _failure(DiscordTaskFailureCategory.RUNTIME_INCOMPATIBLE, _INCOMPATIBLE_SUMMARY)
    if isinstance(error, AgentProcessFailed):
        return _failure(DiscordTaskFailureCategory.AGENT_PROCESS_FAILED, _PROCESS_SUMMARY)
    if isinstance(error, AgentInvalidOutput):
        return _failure(DiscordTaskFailureCategory.INVALID_AGENT_OUTPUT, _INVALID_OUTPUT_SUMMARY)
    if isinstance(error, AgentConfigurationError):
        return _failure(DiscordTaskFailureCategory.CONFIGURATION, _CONFIGURATION_SUMMARY)
    if isinstance(error, AgentWorkspaceOrAttachmentError):
        return _failure(DiscordTaskFailureCategory.WORKSPACE_OR_ATTACHMENT, _WORKSPACE_SUMMARY)
    return _failure(DiscordTaskFailureCategory.INTERNAL, _INTERNAL_SUMMARY)


def classify_delivery_failure(*, definitive_non_delivery: bool) -> DiscordTaskFailure:
    if definitive_non_delivery:
        return DiscordTaskFailure(
            category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
            summary=_RETRY_DELIVERY_SUMMARY,
            retry_mode=DiscordTaskRetryMode.RETRY_DELIVERY,
        )
    return DiscordTaskFailure(
        category=DiscordTaskFailureCategory.DISCORD_DELIVERY,
        summary=_AMBIGUOUS_DELIVERY_SUMMARY,
        retry_mode=DiscordTaskRetryMode.NONE,
    )


def _resumable_failure(
    category: DiscordTaskFailureCategory,
    summary: str,
    *,
    persisted_session: bool,
    active_turn: bool,
) -> DiscordTaskFailure:
    if persisted_session and not active_turn:
        retry_mode = DiscordTaskRetryMode.CONTINUE_SESSION
    elif active_turn:
        summary = _ACTIVE_TURN_SUMMARY
        retry_mode = DiscordTaskRetryMode.NONE
    else:
        summary = _NO_SESSION_SUMMARY
        retry_mode = DiscordTaskRetryMode.NONE
    return DiscordTaskFailure(category=category, summary=summary, retry_mode=retry_mode)


def _failure(category: DiscordTaskFailureCategory, summary: str) -> DiscordTaskFailure:
    return DiscordTaskFailure(
        category=category,
        summary=summary,
        retry_mode=DiscordTaskRetryMode.NONE,
    )
