from pathlib import Path

from study_discord_agent.agent_errors import AgentRuntimeDisconnected
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import ApprovalPolicy, SandboxMode
from study_discord_agent.session_store import ChannelSessionStore


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
) -> str:
    existing = session_store.get(channel_id)
    if require_existing and existing is None:
        raise AgentRuntimeDisconnected("The saved session is unavailable")
    thread = (
        await client.resume_thread(
            existing,
            cwd=cwd,
            model=model,
            model_provider=model_provider,
            approval_policy=approval_policy,
            sandbox=sandbox,
        )
        if existing
        else await client.start_thread(
            cwd=cwd,
            model=model,
            model_provider=model_provider,
            approval_policy=approval_policy,
            sandbox=sandbox,
        )
    )
    session_store.set(channel_id, thread.thread_id)
    return thread.thread_id
