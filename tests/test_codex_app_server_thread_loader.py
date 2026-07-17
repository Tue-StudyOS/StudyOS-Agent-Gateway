from pathlib import Path
from typing import cast

import pytest

from study_discord_agent.agent_errors import AgentRuntimeDisconnected
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import ThreadRef
from study_discord_agent.codex_app_server_thread_loader import load_thread
from study_discord_agent.session_store import ChannelSessionStore


class FakeThreadClient:
    def __init__(self) -> None:
        self.started = 0
        self.resumed: list[str] = []

    async def start_thread(self, **_kwargs: object) -> ThreadRef:
        self.started += 1
        return ThreadRef("new-thread")

    async def resume_thread(self, thread_id: str, **_kwargs: object) -> ThreadRef:
        self.resumed.append(thread_id)
        return ThreadRef(thread_id)


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
