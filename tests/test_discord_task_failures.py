import pytest

from study_discord_agent.agent_errors import (
    AgentConfigurationError,
    AgentInvalidOutput,
    AgentProcessFailed,
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
    AgentTurnTimedOut,
    AgentWorkspaceOrAttachmentError,
)
from study_discord_agent.discord_task_failures import (
    classify_agent_failure,
    classify_delivery_failure,
)
from study_discord_agent.discord_task_model import (
    DiscordTaskFailureCategory,
    DiscordTaskRetryMode,
)


@pytest.mark.parametrize(
    ("error", "category", "summary", "retry_mode"),
    [
        (
            AgentTurnTimedOut("/private/token stderr"),
            DiscordTaskFailureCategory.TIMEOUT,
            "The agent timed out. Partial work and the agent session were kept.",
            DiscordTaskRetryMode.CONTINUE_SESSION,
        ),
        (
            AgentRuntimeDisconnected("/private/token stderr"),
            DiscordTaskFailureCategory.RUNTIME_DISCONNECTED,
            "Codex app server disconnected. The session and worktree were kept.",
            DiscordTaskRetryMode.CONTINUE_SESSION,
        ),
        (
            AgentRuntimeIncompatible("/private/token stderr"),
            DiscordTaskFailureCategory.RUNTIME_INCOMPATIBLE,
            "The configured Codex app server is incompatible.",
            DiscordTaskRetryMode.NONE,
        ),
        (
            AgentProcessFailed("/private/token stderr"),
            DiscordTaskFailureCategory.AGENT_PROCESS_FAILED,
            "The agent process exited before returning a result.",
            DiscordTaskRetryMode.NONE,
        ),
        (
            AgentInvalidOutput("/private/token stderr"),
            DiscordTaskFailureCategory.INVALID_AGENT_OUTPUT,
            "The agent returned an invalid result.",
            DiscordTaskRetryMode.NONE,
        ),
        (
            AgentConfigurationError("/private/token stderr"),
            DiscordTaskFailureCategory.CONFIGURATION,
            "The gateway configuration is incomplete.",
            DiscordTaskRetryMode.NONE,
        ),
        (
            AgentWorkspaceOrAttachmentError("/private/token stderr"),
            DiscordTaskFailureCategory.WORKSPACE_OR_ATTACHMENT,
            "The workspace or attachment could not be prepared safely.",
            DiscordTaskRetryMode.NONE,
        ),
        (
            RuntimeError("/private/token stderr"),
            DiscordTaskFailureCategory.INTERNAL,
            "The task failed safely. Check the task ID with an administrator.",
            DiscordTaskRetryMode.NONE,
        ),
    ],
)
def test_classify_agent_failure_uses_safe_constant_metadata(
    error: BaseException,
    category: DiscordTaskFailureCategory,
    summary: str,
    retry_mode: DiscordTaskRetryMode,
) -> None:
    failure = classify_agent_failure(error, persisted_session=True, active_turn=False)

    assert failure.category is category
    assert failure.summary == summary
    assert failure.retry_mode is retry_mode
    assert "/private/token stderr" not in failure.summary


@pytest.mark.parametrize(
    "error",
    (AgentTurnTimedOut("sensitive"), AgentRuntimeDisconnected("sensitive")),
)
@pytest.mark.parametrize(
    ("persisted_session", "active_turn"),
    ((False, False), (True, True), (False, True)),
)
def test_timeout_and_disconnect_continue_only_for_idle_persisted_sessions(
    error: BaseException, persisted_session: bool, active_turn: bool
) -> None:
    failure = classify_agent_failure(
        error, persisted_session=persisted_session, active_turn=active_turn
    )

    assert failure.retry_mode is DiscordTaskRetryMode.NONE


@pytest.mark.parametrize(
    ("definitive_non_delivery", "summary", "retry_mode"),
    [
        (
            True,
            "Discord could not deliver the result. It is safe to retry delivery.",
            DiscordTaskRetryMode.RETRY_DELIVERY,
        ),
        (
            False,
            "Discord delivery may have succeeded. Check the channel before trying again.",
            DiscordTaskRetryMode.NONE,
        ),
    ],
)
def test_classify_delivery_failure_retries_only_definitive_non_delivery(
    definitive_non_delivery: bool, summary: str, retry_mode: DiscordTaskRetryMode
) -> None:
    failure = classify_delivery_failure(definitive_non_delivery=definitive_non_delivery)

    assert failure.category is DiscordTaskFailureCategory.DISCORD_DELIVERY
    assert failure.summary == summary
    assert failure.retry_mode is retry_mode
