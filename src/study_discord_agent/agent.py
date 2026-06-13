import asyncio
import logging
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx

from study_discord_agent.artifacts import parse_agent_reply, parse_artifact_files
from study_discord_agent.codex_command import (
    AgentCommandResult,
    add_codex_image_args,
    build_codex_resume_args,
    extract_agent_result,
    is_codex_exec_command,
    with_codex_cd_args,
)
from study_discord_agent.discord_worktrees import DiscordWorkspace, DiscordWorktreeManager
from study_discord_agent.prompt_context import build_agent_prompt
from study_discord_agent.session_store import ChannelSessionStore, default_session_store_path
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentReply:
    message: str
    session_id: str | None = None
    files: tuple[Path, ...] = ()


class AgentGateway:
    def __init__(
        self,
        webhook_url: str | None,
        command: str | None,
        workdir: str | None,
        timeout_seconds: int,
        channel_sessions_enabled: bool = True,
        session_store_path: str | None = None,
        codex_home: str | None = None,
        usage_store_path: str | None = None,
        discord_worktree_root: str | None = None,
        studyos_org_root: str = "/workspaces/Tue-StudyOS",
    ) -> None:
        self._webhook_url = webhook_url
        self._command = command
        self._workdir = workdir
        self._timeout_seconds = timeout_seconds
        self._channel_sessions_enabled = channel_sessions_enabled
        self._channel_locks: dict[int, asyncio.Lock] = {}
        store_path = session_store_path or str(default_session_store_path(codex_home))
        self._session_store = ChannelSessionStore(store_path)
        usage_path = usage_store_path or str(default_usage_store_path(codex_home))
        self._usage_store = ChannelUsageStore(usage_path)
        self._discord_worktrees = (
            DiscordWorktreeManager(discord_worktree_root, studyos_org_root)
            if discord_worktree_root
            else None
        )

    async def ask(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None = None,
        attachment_paths: tuple[Path, ...] = (),
    ) -> AgentReply:
        started_at = time.monotonic()
        logger.info("agent request started source_user=%s channel_id=%s", user, channel_id)
        if self._webhook_url:
            reply = await self._ask_webhook(
                prompt,
                user,
                channel_id,
                source_message_id,
                attachment_paths,
            )
        elif self._command:
            reply = await self._ask_command(
                prompt,
                user,
                channel_id,
                source_message_id,
                attachment_paths,
            )
        else:
            raise RuntimeError("Configure AGENT_WEBHOOK_URL or AGENT_COMMAND")

        elapsed = time.monotonic() - started_at
        logger.info(
            "agent request completed source_user=%s channel_id=%s elapsed_seconds=%.2f",
            user,
            channel_id,
            elapsed,
        )
        return reply

    async def _ask_webhook(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None,
        attachment_paths: tuple[Path, ...],
    ) -> AgentReply:
        if not self._webhook_url:
            raise RuntimeError("AGENT_WEBHOOK_URL is not configured")

        payload = {
            "prompt": prompt,
            "source": "discord",
            "user": user,
            "channel_id": channel_id,
            "source_message_id": source_message_id,
            "attachments": [str(path) for path in attachment_paths],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self._webhook_url, json=payload)
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())

        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RuntimeError("Agent response must contain a non-empty message")
        parsed = parse_agent_reply(message)
        files = parsed.files + parse_artifact_files(data.get("files"))
        return AgentReply(message=parsed.message, files=files)

    async def _ask_command(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None,
        attachment_paths: tuple[Path, ...],
    ) -> AgentReply:
        if not self._command:
            raise RuntimeError("AGENT_COMMAND is not configured")

        args = shlex.split(self._command)
        workspace = await self._prepare_discord_workspace(
            args,
            prompt,
            channel_id,
            source_message_id,
        )
        if workspace:
            args = with_codex_cd_args(args, workspace.path)
        full_prompt = build_agent_prompt(
            prompt,
            user,
            channel_id,
            os.environ.get("CODEX_HOME"),
            source_message_id,
            tuple(str(path) for path in attachment_paths),
            str(workspace.path) if workspace else None,
        )
        image_paths = tuple(path for path in attachment_paths if _is_image_path(path))
        if self._uses_channel_sessions(args, channel_id, source_message_id):
            assert channel_id is not None
            lock = self._channel_locks.setdefault(channel_id, asyncio.Lock())
            async with lock:
                return await self._ask_codex_channel_session(
                    args,
                    full_prompt,
                    channel_id,
                    image_paths,
                )

        run_args = add_codex_image_args(args, image_paths) if is_codex_exec_command(args) else args
        result = await self._run_command(run_args, full_prompt)
        if channel_id is not None:
            self._record_usage(channel_id, result)
        return self._agent_reply_from_result(result)

    async def _ask_codex_channel_session(
        self,
        args: list[str],
        full_prompt: str,
        channel_id: int,
        image_paths: tuple[Path, ...],
    ) -> AgentReply:
        session_id = self._session_store.get(channel_id)
        run_args = (
            build_codex_resume_args(args, session_id, image_paths)
            if session_id
            else add_codex_image_args(args, image_paths)
        )
        result = await self._run_command(run_args, full_prompt)
        if result.session_id:
            self._session_store.set(channel_id, result.session_id)
        self._record_usage(channel_id, result)
        return self._agent_reply_from_result(result)

    async def _run_command(self, args: list[str], full_prompt: str) -> AgentCommandResult:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workdir,
        )
        logger.info("agent command spawned pid=%s command=%s", process.pid, shlex.join(args))
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(full_prompt.encode("utf-8")),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Agent command timed out") from None

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("agent command failed returncode=%s error=%s", process.returncode, error)
            raise RuntimeError(f"Agent command failed: {error[:1000]}")

        output = stdout.decode("utf-8", errors="replace").strip()
        result = extract_agent_result(output)
        if not result.message:
            raise RuntimeError("Agent command produced no output")
        return result

    def _uses_channel_sessions(
        self,
        args: list[str],
        channel_id: int | None,
        source_message_id: int | None,
    ) -> bool:
        return (
            self._channel_sessions_enabled
            and channel_id is not None
            and source_message_id is not None
            and is_codex_exec_command(args)
        )

    async def _prepare_discord_workspace(
        self,
        args: list[str],
        prompt: str,
        channel_id: int | None,
        source_message_id: int | None,
    ) -> DiscordWorkspace | None:
        if (
            self._discord_worktrees is None
            or channel_id is None
            or source_message_id is None
            or not is_codex_exec_command(args)
        ):
            return None
        workspace = await self._discord_worktrees.prepare(prompt, channel_id)
        logger.info(
            "prepared Discord workspace channel_id=%s path=%s repo=%s",
            channel_id,
            workspace.path,
            workspace.repo_name,
        )
        return workspace

    def _agent_reply_from_result(self, result: AgentCommandResult) -> AgentReply:
        parsed = parse_agent_reply(result.message)
        if not parsed.message and not parsed.files:
            raise RuntimeError("Agent command produced an empty artifact response")
        return AgentReply(
            message=parsed.message,
            session_id=result.session_id,
            files=parsed.files,
        )

    def _record_usage(self, channel_id: int, result: AgentCommandResult) -> None:
        self._usage_store.add(channel_id, result.usage, result.session_id)


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
