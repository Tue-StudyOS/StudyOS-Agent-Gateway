import sys
from pathlib import Path

import pytest

from study_discord_agent.agent import AgentGateway
from study_discord_agent.codex_command import (
    add_codex_image_args,
    build_codex_resume_args,
    extract_agent_result,
    with_codex_cd_args,
)


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
