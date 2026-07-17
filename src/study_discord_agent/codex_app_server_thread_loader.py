from pathlib import Path

from study_discord_agent.agent_errors import (
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
)
from study_discord_agent.agent_execution_policy import AgentExecutionPolicy
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import ApprovalPolicy, SandboxMode, ThreadRef
from study_discord_agent.session_store import ChannelSessionBinding, ChannelSessionStore


async def load_thread(
    client: CodexAppServerClient,
    session_store: ChannelSessionStore,
    channel_id: int,
    cwd: str | Path | None,
    *,
    model: str | None,
    model_provider: str | None,
    approval_policy: ApprovalPolicy | None,
    sandbox: SandboxMode | None,
    require_existing: bool = False,
    execution_policy: AgentExecutionPolicy | None = None,
    repository_full_name: str | None = None,
    commit_sha: str | None = None,
) -> str:
    expected = _restricted_binding(
        execution_policy,
        repository_full_name,
        commit_sha,
        cwd,
    )
    existing = session_store.get_binding(channel_id)
    if expected is not None and existing is not None and not _matches(existing, expected):
        existing = None
    if require_existing and existing is None:
        raise AgentRuntimeDisconnected("The saved session is unavailable")
    roots = (cwd,) if expected is not None and cwd is not None else None
    effective_approval = (
        execution_policy.approval_policy if execution_policy else approval_policy
    )
    effective_sandbox = execution_policy.sandbox_mode if execution_policy else sandbox
    thread = await _open_thread(
        client,
        existing,
        cwd,
        model=model,
        model_provider=model_provider,
        approval_policy=effective_approval,
        sandbox=effective_sandbox,
        restricted=execution_policy is not None,
        runtime_workspace_roots=roots,
    )
    if execution_policy is not None:
        _validate_effective_policy(thread, execution_policy)
        assert expected is not None
        session_store.set_binding(
            channel_id,
            ChannelSessionBinding(session_id=thread.thread_id, **expected),
        )
    else:
        session_store.set(channel_id, thread.thread_id)
    return thread.thread_id


async def _open_thread(
    client: CodexAppServerClient,
    existing: ChannelSessionBinding | None,
    cwd: str | Path | None,
    *,
    model: str | None,
    model_provider: str | None,
    approval_policy: ApprovalPolicy | None,
    sandbox: SandboxMode | None,
    restricted: bool,
    runtime_workspace_roots: tuple[str | Path, ...] | None,
) -> ThreadRef:
    if existing is not None:
        return await client.resume_thread(
            existing.session_id,
            cwd=cwd,
            model=model,
            model_provider=model_provider,
            approval_policy=approval_policy,
            sandbox=sandbox,
            runtime_workspace_roots=runtime_workspace_roots,
        )
    return await client.start_thread(
        cwd=cwd,
        model=model,
        model_provider=model_provider,
        approval_policy=approval_policy,
        sandbox=sandbox,
        dynamic_tools=() if restricted else None,
        environments=() if restricted else None,
        runtime_workspace_roots=runtime_workspace_roots,
    )


def _restricted_binding(
    policy: AgentExecutionPolicy | None,
    repository_full_name: str | None,
    commit_sha: str | None,
    cwd: str | Path | None,
) -> dict[str, str] | None:
    if policy is None:
        return None
    if repository_full_name is None or commit_sha is None or cwd is None:
        raise ValueError("Restricted execution binding is incomplete")
    workspace = Path(cwd)
    if not workspace.is_absolute():
        raise ValueError("Restricted execution workspace must be absolute")
    return {
        "policy_class": policy.policy_class.value,
        "policy_fingerprint": policy.fingerprint,
        "repository_full_name": repository_full_name,
        "commit_sha": commit_sha,
        "workspace_path": str(workspace),
    }


def _matches(binding: ChannelSessionBinding, expected: dict[str, str]) -> bool:
    return all(getattr(binding, key) == value for key, value in expected.items())


def _validate_effective_policy(
    thread: ThreadRef,
    policy: AgentExecutionPolicy,
) -> None:
    if (
        thread.approval_policy != policy.approval_policy
        or thread.sandbox_policy != policy.sandbox_policy
    ):
        raise AgentRuntimeIncompatible(
            "Codex app-server did not apply the restricted execution policy"
        )
