import asyncio
import json
import logging
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from study_discord_agent.codex_command import (
    AgentCommandResult,
    extract_agent_result,
    extract_session_id_from_event,
)

logger = logging.getLogger(__name__)


async def run_agent_command(
    args: list[str],
    full_prompt: str,
    workdir: str | None,
    timeout_seconds: int,
    on_session_id: Callable[[str], None] | None = None,
) -> AgentCommandResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
    )
    logger.info("agent command spawned pid=%s command=%s", process.pid, shlex.join(args))
    try:
        stdout, stderr = await asyncio.wait_for(
            _communicate_streamed(process, full_prompt, on_session_id),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        raise RuntimeError("Agent command timed out") from None

    if process.returncode != 0:
        error = stderr.strip()
        logger.warning("agent command failed returncode=%s error=%s", process.returncode, error)
        raise RuntimeError(f"Agent command failed: {error[:1000]}")

    output = stdout.strip()
    result = extract_agent_result(output)
    if not result.message:
        raise RuntimeError("Agent command produced no output")
    return result


async def _communicate_streamed(
    process: asyncio.subprocess.Process,
    full_prompt: str,
    on_session_id: Callable[[str], None] | None,
) -> tuple[str, str]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("Agent command streams were not created")

    stdin_task = asyncio.create_task(_write_stdin(process.stdin, full_prompt))
    stdout_task = asyncio.create_task(_read_stdout(process.stdout, on_session_id))
    stderr_task = asyncio.create_task(_read_stderr(process.stderr))
    try:
        await process.wait()
        await asyncio.gather(stdin_task, return_exceptions=True)
        return await asyncio.gather(stdout_task, stderr_task)
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        if process.returncode is None:
            await _terminate_process(process)
        await _cancel_stream_tasks(stdin_task, stdout_task, stderr_task)


async def _write_stdin(stdin: asyncio.StreamWriter, full_prompt: str) -> None:
    try:
        stdin.write(full_prompt.encode("utf-8"))
        stdin.close()
        await stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass


async def _read_stdout(
    stdout: asyncio.StreamReader,
    on_session_id: Callable[[str], None] | None,
) -> str:
    lines: list[str] = []
    seen_session_id: str | None = None
    while line_bytes := await stdout.readline():
        line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
        lines.append(line)
        session_id = _session_id_from_jsonl(line)
        if session_id and session_id != seen_session_id:
            seen_session_id = session_id
            if on_session_id:
                on_session_id(session_id)
    return "\n".join(lines)


async def _read_stderr(stderr: asyncio.StreamReader) -> str:
    data = await stderr.read()
    return data.decode("utf-8", errors="replace")


def _session_id_from_jsonl(line: str) -> str | None:
    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return extract_session_id_from_event(cast(dict[str, object], parsed))


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _cancel_stream_tasks(*tasks: asyncio.Task[Any]) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
