import asyncio
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentGateway
from study_discord_agent.session_store import ChannelSessionStore
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path


@pytest.mark.asyncio
async def test_codex_channel_session_uses_discord_worktree_root(tmp_path: Path) -> None:
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
        session_store_path=str(tmp_path / "sessions.json"),
        discord_worktree_root=str(root),
        studyos_org_root=str(tmp_path / "Tue-StudyOS"),
    )

    reply = await agent.ask("hello", user="student", channel_id=123, source_message_id=1)

    assert f"--cd {root / '123'}" in reply.message
    assert (root / "123").is_dir()


@pytest.mark.asyncio
async def test_codex_channel_session_resumes_after_first_turn(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "session = 'stored-session'",
                "if 'resume' in sys.argv:",
                "    text = 'resumed:' + ('stored-session' if session in sys.argv else 'missing')",
                "else:",
                "    text = 'started'",
                "print(json.dumps({'type': 'session_meta', 'payload': {'id': session}}))",
                "print(json.dumps({'item': {'type': 'agent_message', 'text': text}}))",
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
        session_store_path=str(tmp_path / "sessions.json"),
    )

    first = await agent.ask("hello", user="student", channel_id=123, source_message_id=1)
    second = await agent.ask("again", user="student", channel_id=123, source_message_id=2)

    assert first.message == "started"
    assert first.session_id == "stored-session"
    assert second.message == "resumed:stored-session"


@pytest.mark.asyncio
async def test_codex_session_is_persisted_before_cancel_and_resume(tmp_path: Path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "import time",
                "sys.stdin.read()",
                "session = 'session-early'",
                "if 'resume' in sys.argv:",
                "    text = 'resumed:' + ('session-early' if session in sys.argv else 'missing')",
                "    print(json.dumps({'type': 'session_meta', "
                "'payload': {'id': session}}), flush=True)",
                "    print(json.dumps({'item': {'type': 'agent_message', "
                "'text': text}}), flush=True)",
                "else:",
                "    print(json.dumps({'type': 'session_meta', "
                "'payload': {'id': session}}), flush=True)",
                "    time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    session_path = tmp_path / "sessions.json"
    agent = AgentGateway(
        webhook_url=None,
        command=f"{fake_codex} exec --json --cd /workspace -",
        workdir=None,
        timeout_seconds=10,
        session_store_path=str(session_path),
    )

    first = asyncio.create_task(
        agent.ask("hello", user="student", channel_id=123, source_message_id=1)
    )
    store = ChannelSessionStore(session_path)
    for _ in range(100):
        if store.get(123) == "session-early":
            break
        await asyncio.sleep(0.01)
    assert store.get(123) == "session-early"

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = await agent.ask("again", user="student", channel_id=123, source_message_id=2)

    assert second.message == "resumed:session-early"


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
async def test_codex_usage_is_recorded_by_channel(tmp_path: Path) -> None:
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
        usage_store_path=str(usage_path),
    )

    reply = await agent.ask("hello", user="student", channel_id=123, source_message_id=1)

    rows = ChannelUsageStore(usage_path).rows()
    assert reply.message == "done"
    assert len(rows) == 1
    assert rows[0].channel_id == 123
    assert rows[0].turns == 1
    assert rows[0].input_tokens == 100
    assert rows[0].cached_input_tokens == 25
    assert rows[0].output_tokens == 7
    assert rows[0].reasoning_output_tokens == 2
    assert rows[0].last_session_id == "session-123"


@pytest.mark.asyncio
async def test_positional_codex_home_controls_default_usage_store(tmp_path: Path) -> None:
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
    agent = AgentGateway(None, f"{fake_codex} exec --json -", None, 10, True, None, str(codex_home))

    await agent.ask("hello", user="student", channel_id=456, source_message_id=1)

    rows = ChannelUsageStore(default_usage_store_path(str(codex_home))).rows()
    assert len(rows) == 1
    assert rows[0].channel_id == 456
    assert rows[0].total_tokens == 15
