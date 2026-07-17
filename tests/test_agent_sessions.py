import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from study_discord_agent.agent import (
    AgentChannelCapabilities,
    AgentExecutionContext,
    AgentGateway,
)
from study_discord_agent.codex_app_server_runtime import SteerResult
from study_discord_agent.codex_app_server_turn import AppServerTurnResult
from study_discord_agent.codex_command import AgentUsage
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path


@dataclass(frozen=True)
class _RuntimeCall:
    channel_id: int
    cwd: str | Path | None
    require_existing_thread: bool


class _FakePersistentRuntime:
    def __init__(self) -> None:
        self.calls: list[_RuntimeCall] = []
        self.steers: list[tuple[int, str]] = []

    async def run(
        self,
        *,
        channel_id: int,
        prompt: str,
        cwd: str | Path | None,
        local_images: tuple[Path, ...] = (),
        on_progress: object = None,
        require_existing_thread: bool = False,
    ) -> AppServerTurnResult:
        del prompt, local_images, on_progress
        self.calls.append(
            _RuntimeCall(
                channel_id=channel_id,
                cwd=cwd,
                require_existing_thread=require_existing_thread,
            )
        )
        return AppServerTurnResult(
            message="done",
            thread_id="thread-1",
            usage=AgentUsage(input_tokens=2, output_tokens=1),
        )

    async def steer(
        self,
        *,
        channel_id: int,
        prompt: str,
        local_images: tuple[Path, ...] = (),
    ) -> SteerResult:
        del local_images
        self.steers.append((channel_id, prompt))
        return SteerResult.STEERED


class _CapabilityRuntime:
    def __init__(self, *, active_turn: bool, persisted_session: bool) -> None:
        self._active_turn = active_turn
        self._persisted_session = persisted_session

    async def has_active_turn(self, channel_id: int) -> bool:
        assert channel_id == 123
        return self._active_turn

    def has_persisted_session(self, channel_id: int) -> bool:
        assert channel_id == 123
        return self._persisted_session


@pytest.mark.asyncio
async def test_source_less_discord_execution_uses_app_server_and_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "discord-worktrees"
    usage_path = tmp_path / "usage.json"
    agent = AgentGateway(
        webhook_url=None,
        command="codex exec --json -",
        workdir=None,
        timeout_seconds=10,
        discord_worktree_root=str(root),
        studyos_org_root=str(tmp_path / "Tue-StudyOS"),
        usage_store_path=str(usage_path),
    )
    fake_runtime = _FakePersistentRuntime()
    monkeypatch.setattr(agent, "_codex_runtime", cast(Any, fake_runtime))

    reply = await agent.ask(
        "continue",
        user="student",
        channel_id=123,
        source_message_id=None,
        execution=AgentExecutionContext(channel_id=123, trigger_event_id=9001),
    )

    assert reply.session_id == "thread-1"
    assert fake_runtime.calls[0].channel_id == 123
    assert Path(cast(str, fake_runtime.calls[0].cwd)).name == "123"
    assert not fake_runtime.calls[0].require_existing_thread
    assert ChannelUsageStore(usage_path).rows()[0].channel_id == 123


@pytest.mark.asyncio
async def test_webhook_channel_metadata_without_execution_remains_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path
    command = f"{sys.executable} -c \"print('done')\""
    agent = AgentGateway(
        webhook_url=None,
        command=command,
        workdir=None,
        timeout_seconds=10,
    )
    fake_runtime = _FakePersistentRuntime()
    monkeypatch.setattr(agent, "_codex_runtime", cast(Any, fake_runtime))

    reply = await agent.ask("review", user="github", channel_id=123)

    assert reply.message == "done"
    assert fake_runtime.calls == []


@pytest.mark.asyncio
async def test_source_less_steering_reaches_persistent_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = AgentGateway(
        webhook_url=None,
        command="codex exec --json -",
        workdir=None,
        timeout_seconds=10,
    )
    fake_runtime = _FakePersistentRuntime()
    monkeypatch.setattr(agent, "_codex_runtime", cast(Any, fake_runtime))

    result = await agent.steer(
        prompt="use the updated scope",
        user="student",
        channel_id=123,
        source_message_id=None,
    )

    assert result is SteerResult.STEERED
    assert fake_runtime.steers[0][0] == 123


@pytest.mark.asyncio
async def test_channel_capabilities_reflect_persistent_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = AgentGateway(None, "codex exec --json -", None, 10)
    runtime = _CapabilityRuntime(active_turn=False, persisted_session=True)
    monkeypatch.setattr(agent, "_codex_runtime", cast(Any, runtime))

    capabilities = await agent.channel_capabilities(123)

    assert capabilities == AgentChannelCapabilities(
        steering=False,
        resumable=True,
        persisted_session=True,
        active_turn=False,
    )


@pytest.mark.asyncio
async def test_webhook_metadata_does_not_prepare_discord_worktree(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "print(json.dumps({'type': 'session_meta', 'payload': {'id': 's'}}))",
                "text = ' '.join(sys.argv)",
                "print(json.dumps({'item': {'type': 'agent_message', 'text': text}}))",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    root = tmp_path / "discord-worktrees"
    agent = AgentGateway(
        webhook_url=None,
        command=f"{fake_codex} exec --json --cd /workspace -",
        workdir=None,
        timeout_seconds=10,
        channel_sessions_enabled=False,
        session_store_path=str(tmp_path / "sessions.json"),
        discord_worktree_root=str(root),
        studyos_org_root=str(tmp_path / "Tue-StudyOS"),
    )

    reply = await agent.ask("hello", user="student", channel_id=123, source_message_id=1)

    assert "--cd /workspace" in reply.message
    assert not (root / "123").exists()


@pytest.mark.asyncio
async def test_codex_channel_sessions_run_different_channels_in_parallel(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    starts_log = tmp_path / "starts.log"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "import time",
                "sys.stdin.read()",
                f"with open({str(starts_log)!r}, 'a', encoding='utf-8') as log:",
                "    log.write(f'{time.monotonic()}\\n')",
                "time.sleep(0.4)",
                "print(json.dumps({'type': 'session_meta', 'payload': {'id': 's'}}))",
                "print(json.dumps({'item': {'type': 'agent_message', 'text': 'done'}}))",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    agent = AgentGateway(
        webhook_url=None,
        command=f"{fake_codex} exec --json --cd /workspace -",
        workdir=None,
        timeout_seconds=10,
        channel_sessions_enabled=False,
        session_store_path=str(tmp_path / "sessions.json"),
    )

    first, second = await asyncio.gather(
        agent.ask("one", user="student", channel_id=101, source_message_id=1),
        agent.ask("two", user="student", channel_id=202, source_message_id=2),
    )
    starts = [float(line) for line in starts_log.read_text(encoding="utf-8").splitlines()]

    assert first.message == "done"
    assert second.message == "done"
    assert len(starts) == 2
    assert max(starts) - min(starts) < 0.35


@pytest.mark.asyncio
async def test_webhook_metadata_does_not_record_channel_usage(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "print(json.dumps({'type': 'thread.started', 'thread_id': 'session-123'}))",
                "print(json.dumps({'item': {'type': 'agent_message', 'text': 'done'}}))",
                "print(json.dumps({'type': 'turn.completed', 'usage': {",
                "    'input_tokens': 100,",
                "    'cached_input_tokens': 25,",
                "    'output_tokens': 7,",
                "    'reasoning_output_tokens': 2,",
                "}}))",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    usage_path = tmp_path / "usage.json"
    agent = AgentGateway(
        webhook_url=None,
        command=f"{fake_codex} exec --json -",
        workdir=None,
        timeout_seconds=10,
        channel_sessions_enabled=False,
        usage_store_path=str(usage_path),
    )

    reply = await agent.ask("hello", user="student", channel_id=123, source_message_id=1)

    rows = ChannelUsageStore(usage_path).rows()
    assert reply.message == "done"
    assert rows == ()


@pytest.mark.asyncio
async def test_webhook_metadata_does_not_create_default_usage_store(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "print(json.dumps({'type': 'thread.started', 'thread_id': 'session-456'}))",
                "print(json.dumps({'item': {'type': 'agent_message', 'text': 'done'}}))",
                "print(json.dumps({'type': 'turn.completed', 'usage': {",
                "    'input_tokens': 12,",
                "    'output_tokens': 3,",
                "}}))",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    codex_home = tmp_path / "codex-home"
    agent = AgentGateway(
        None,
        f"{fake_codex} exec --json -",
        None,
        10,
        False,
        None,
        str(codex_home),
    )

    await agent.ask("hello", user="student", channel_id=456, source_message_id=1)

    rows = ChannelUsageStore(default_usage_store_path(str(codex_home))).rows()
    assert rows == ()
