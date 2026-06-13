import sys
from pathlib import Path

import pytest

from study_discord_agent.agent import (
    AgentGateway,
    add_codex_image_args,
    build_codex_resume_args,
    extract_agent_result,
    with_codex_cd_args,
)
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path


@pytest.mark.asyncio
async def test_agent_command_receives_prompt_on_stdin() -> None:
    agent = AgentGateway(
        webhook_url=None,
        command=f"{sys.executable} -c \"import sys; print(sys.stdin.read().upper())\"",
        workdir=None,
        timeout_seconds=10,
    )

    reply = await agent.ask("hello course", user="student", channel_id=123)

    assert "HELLO COURSE" in reply.message


@pytest.mark.asyncio
async def test_agent_command_extracts_final_json_message() -> None:
    command = (
        f"{sys.executable} -c "
        "\"print('{\\\"type\\\":\\\"item.completed\\\",\\\"item\\\":{\\\"type\\\":"
        "\\\"agent_message\\\",\\\"text\\\":\\\"final answer\\\"}}')\""
    )
    agent = AgentGateway(
        webhook_url=None,
        command=command,
        workdir=None,
        timeout_seconds=10,
    )

    reply = await agent.ask("hello course", user="student", channel_id=123)

    assert reply.message == "final answer"


def test_extract_agent_result_reads_session_id() -> None:
    output = "\n".join(
        [
            '{"type":"session_meta","payload":{"id":"session-123"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
        ]
    )

    result = extract_agent_result(output)

    assert result.session_id == "session-123"
    assert result.message == "done"


def test_extract_agent_result_reads_thread_started_id() -> None:
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"019eacf7-09ab-7f71-9d6c-a5e61d9d4d3e"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
        ]
    )

    result = extract_agent_result(output)

    assert result.session_id == "019eacf7-09ab-7f71-9d6c-a5e61d9d4d3e"
    assert result.message == "done"


def test_extract_agent_result_reads_turn_usage() -> None:
    output = "\n".join(
        [
            '{"type":"thread.started","thread_id":"session-123"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":100,'
            '"cached_input_tokens":20,"output_tokens":7,"reasoning_output_tokens":3}}',
        ]
    )

    result = extract_agent_result(output)

    assert result.usage.input_tokens == 100
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 7
    assert result.usage.reasoning_output_tokens == 3
    assert result.usage.total_tokens == 107


def test_build_codex_resume_args_keeps_supported_options() -> None:
    args = [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        "/workspace",
        "-",
    ]

    resume_args = build_codex_resume_args(args, "session-123")

    assert resume_args == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "session-123",
        "-",
    ]


def test_codex_image_args_are_inserted_before_prompt() -> None:
    args = ["codex", "exec", "--json", "-"]
    image = Path("/tmp/studyos-discord-attachments/1/input.png")

    assert add_codex_image_args(args, (image,)) == [
        "codex",
        "exec",
        "--json",
        "-i",
        str(image),
        "-",
    ]


def test_codex_resume_args_include_image_inputs() -> None:
    args = ["codex", "exec", "--json", "--cd", "/workspace", "-"]
    image = Path("/tmp/studyos-discord-attachments/1/input.png")

    resume_args = build_codex_resume_args(args, "session-123", (image,))

    assert resume_args == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-i",
        str(image),
        "session-123",
        "-",
    ]


def test_codex_cd_args_replace_existing_workspace() -> None:
    args = ["codex", "exec", "--json", "--cd", "/workspace", "-"]
    worktree = Path("/workspaces/.studyos-discord-worktrees/123/example")

    assert with_codex_cd_args(args, worktree) == [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(worktree),
        "-",
    ]


def test_codex_cd_args_are_inserted_before_prompt() -> None:
    args = ["codex", "exec", "--json", "-"]
    worktree = Path("/workspaces/.studyos-discord-worktrees/123/example")

    assert with_codex_cd_args(args, worktree) == [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(worktree),
        "-",
    ]


@pytest.mark.asyncio
async def test_agent_command_extracts_artifact_reply(tmp_path: Path) -> None:
    artifact = tmp_path / "diagram.png"
    artifact.write_bytes(b"png")
    command = (
        f"{sys.executable} -c "
        f"\"import json; print(json.dumps({{'message':'done','files':['{artifact}']}}))\""
    )
    agent = AgentGateway(
        webhook_url=None,
        command=command,
        workdir=None,
        timeout_seconds=10,
    )

    reply = await agent.ask("make diagram", user="student", channel_id=123)

    assert reply.message == "done"
    assert reply.files == (artifact,)


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
