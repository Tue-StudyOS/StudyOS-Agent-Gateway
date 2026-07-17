from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.agent_errors import (
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
)
from study_discord_agent.agent_execution_policy import (
    AgentPolicyClass,
    execution_policy,
)
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import ThreadRef
from study_discord_agent.codex_app_server_thread_loader import load_thread
from study_discord_agent.session_store import ChannelSessionStore


class FakeThreadClient:
    def __init__(self) -> None:
        self.started = 0
        self.resumed: list[str] = []
        self.start_kwargs: dict[str, object] = {}
        self.resume_kwargs: dict[str, object] = {}
        self.thread = ThreadRef("new-thread")

    async def start_thread(self, **kwargs: object) -> ThreadRef:
        self.started += 1
        self.start_kwargs = kwargs
        return self.thread

    async def resume_thread(self, thread_id: str, **kwargs: object) -> ThreadRef:
        self.resumed.append(thread_id)
        self.resume_kwargs = kwargs
        return self.thread


@pytest.mark.asyncio
async def test_required_existing_thread_never_falls_back_to_a_new_thread(
    tmp_path: Path,
) -> None:
    client = FakeThreadClient()
    sessions = ChannelSessionStore(tmp_path / "sessions.json")

    with pytest.raises(AgentRuntimeDisconnected, match="saved session"):
        await load_thread(
            cast(CodexAppServerClient, client),
            sessions,
            channel_id=10,
            cwd=tmp_path,
            model=None,
            model_provider=None,
            approval_policy=None,
            sandbox=None,
            require_existing=True,
        )

    assert client.started == 0
    assert client.resumed == []


@pytest.mark.asyncio
async def test_restricted_thread_uses_only_an_exact_policy_binding(tmp_path: Path) -> None:
    client = FakeThreadClient()
    sessions = ChannelSessionStore(tmp_path / "sessions.json")
    sessions.set(10, "legacy-thread")
    policy = execution_policy(AgentPolicyClass.SECURITY_REVIEW)
    client.thread = ThreadRef(
        "restricted-thread",
        "never",
        policy.sandbox_policy,
        "studyos-restricted",
    )

    thread_id = await load_thread(
        cast(CodexAppServerClient, client),
        sessions,
        channel_id=10,
        cwd=tmp_path,
        model=None,
        model_provider=None,
        approval_policy=None,
        sandbox=None,
        execution_policy=policy,
        repository_full_name="Tue-StudyOS/example",
        commit_sha="a" * 40,
    )

    assert thread_id == "restricted-thread"
    assert client.resumed == []
    assert client.start_kwargs["approval_policy"] == "never"
    assert client.start_kwargs["sandbox"] is None
    assert client.start_kwargs["permissions"] == "studyos-restricted"
    assert client.start_kwargs["dynamic_tools"] == ()
    assert client.start_kwargs["environments"] == ()
    binding = sessions.get_binding(10)
    assert binding is not None
    assert binding.policy_fingerprint == policy.fingerprint

    client.thread = ThreadRef(
        "restricted-thread",
        "on-request",
        policy.sandbox_policy,
        "studyos-restricted",
    )
    with pytest.raises(AgentRuntimeIncompatible, match="did not apply"):
        await load_thread(
            cast(CodexAppServerClient, client),
            sessions,
            channel_id=10,
            cwd=tmp_path,
            model=None,
            model_provider=None,
            approval_policy=None,
            sandbox=None,
            execution_policy=policy,
            repository_full_name="Tue-StudyOS/example",
            commit_sha="a" * 40,
        )

    assert client.resumed == ["restricted-thread"]


@pytest.mark.asyncio
async def test_restricted_thread_isolates_shell_environment_on_start_and_resume(
    tmp_path: Path,
) -> None:
    client = FakeThreadClient()
    sessions = ChannelSessionStore(tmp_path / "sessions.json")
    policy = execution_policy(AgentPolicyClass.SECURITY_REVIEW)
    client.thread = ThreadRef(
        "restricted-thread",
        "never",
        policy.sandbox_policy,
        "studyos-restricted",
    )
    expected_config = {
        "allow_login_shell": False,
        "permissions": {
            "studyos-restricted": {
                "extends": ":read-only",
                "filesystem": {
                    ":workspace_roots": {"**/.env*": "deny"},
                    "/auth": "deny",
                    "/proc": "deny",
                    "/run/secrets": "deny",
                },
                "network": {"enabled": False},
            },
        },
        "shell_environment_policy": {
            "inherit": "none",
            "set": {
                "LANG": "C.UTF-8",
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
        },
    }

    async def open_restricted_thread() -> None:
        await load_thread(
            cast(CodexAppServerClient, client),
            sessions,
            channel_id=10,
            cwd=tmp_path,
            model=None,
            model_provider=None,
            approval_policy=None,
            sandbox=None,
            execution_policy=policy,
            repository_full_name="Tue-StudyOS/example",
            commit_sha="a" * 40,
        )

    await open_restricted_thread()
    await open_restricted_thread()

    assert client.start_kwargs["config"] == expected_config
    assert client.resume_kwargs["config"] == expected_config
    assert client.start_kwargs["permissions"] == "studyos-restricted"
    assert client.resume_kwargs["permissions"] == "studyos-restricted"
    assert client.start_kwargs["sandbox"] is None
    assert client.resume_kwargs["sandbox"] is None


@pytest.mark.asyncio
async def test_implementation_accepts_app_server_workspace_policy_metadata(
    tmp_path: Path,
) -> None:
    client = FakeThreadClient()
    policy = execution_policy(AgentPolicyClass.IMPLEMENTATION)
    client.thread = ThreadRef(
        "implementation-thread",
        "never",
        {
            **policy.sandbox_policy,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        },
        "studyos-restricted",
    )

    thread_id = await load_thread(
        cast(CodexAppServerClient, client),
        ChannelSessionStore(tmp_path / "sessions.json"),
        channel_id=20,
        cwd=tmp_path,
        model=None,
        model_provider=None,
        approval_policy=None,
        sandbox=None,
        execution_policy=policy,
        repository_full_name="Tue-StudyOS/example",
        commit_sha="b" * 40,
    )

    assert thread_id == "implementation-thread"
