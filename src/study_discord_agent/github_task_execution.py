from pathlib import Path

from study_discord_agent.agent import AgentExecutionContext
from study_discord_agent.agent_errors import AgentConfigurationError
from study_discord_agent.agent_execution_policy import AgentPolicyClass, execution_policy
from study_discord_agent.discord_task_execution import default_execution_context
from study_discord_agent.discord_task_model import DiscordTaskIntent, DiscordTaskRecord
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.github_task_context import resolve_github_task_context

_POLICY_BY_INTENT = {
    DiscordTaskIntent.REVIEW: AgentPolicyClass.REVIEW,
    DiscordTaskIntent.SECURITY_REVIEW: AgentPolicyClass.SECURITY_REVIEW,
    DiscordTaskIntent.VULNERABILITY_SCAN: AgentPolicyClass.VULNERABILITY_SCAN,
    DiscordTaskIntent.IMPLEMENTATION: AgentPolicyClass.IMPLEMENTATION,
}


class GitHubTaskExecutionResolver:
    def __init__(self, mirror_store: GitHubMirrorStore, canonical_root: Path) -> None:
        self._mirror_store = mirror_store
        self._canonical_root = canonical_root

    async def __call__(self, record: DiscordTaskRecord) -> AgentExecutionContext:
        if record.intent is DiscordTaskIntent.GENERAL:
            return default_execution_context(record)
        if record.source_reference_id is None or record.repository_commit_sha is None:
            raise AgentConfigurationError(
                "Repository tasks require a persisted source and commit"
            )
        policy_class = _POLICY_BY_INTENT.get(record.intent)
        if policy_class is None:
            raise AgentConfigurationError("The persisted task intent is unsupported")
        try:
            mirror = self._mirror_store.get(record.source_reference_id)
        except KeyError as error:
            raise AgentConfigurationError(
                "The persisted GitHub source is no longer available"
            ) from error
        context = await resolve_github_task_context(
            mirror,
            self._canonical_root,
            pinned_commit_sha=record.repository_commit_sha,
        )
        return AgentExecutionContext(
            channel_id=record.execution_channel_id,
            trigger_event_id=record.trigger_event_id,
            repository_full_name=context.repository_full_name,
            repository_commit_sha=context.commit_sha,
            execution_policy=execution_policy(policy_class),
        )
