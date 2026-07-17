import logging
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.agent_errors import (
    AgentConfigurationError,
    AgentInvalidOutput,
    AgentWorkspaceOrAttachmentError,
)
from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.agent_webhook import request_agent_webhook
from study_discord_agent.artifacts import parse_agent_reply
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_command import parse_codex_app_server_command
from study_discord_agent.codex_app_server_runtime import (
    CodexAppServerRuntime,
    SteerResult,
)
from study_discord_agent.codex_command import (
    AgentCommandResult,
    add_codex_image_args,
    is_codex_exec_command,
    with_codex_cd_args,
)
from study_discord_agent.command_runner import is_image_path, run_agent_command
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_worktrees import DiscordWorkspace, DiscordWorktreeManager
from study_discord_agent.prompt_context import build_agent_prompt
from study_discord_agent.session_store import ChannelSessionStore, default_session_store_path
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path

logger = logging.getLogger(__name__)
ProgressSink = Callable[[AgentProgress], Awaitable[None]]


@dataclass(frozen=True)
class AgentReply:
    message: str
    session_id: str | None = None
    files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AgentExecutionContext:
    channel_id: int
    trigger_event_id: int


@dataclass(frozen=True)
class AgentChannelCapabilities:
    steering: bool
    resumable: bool
    persisted_session: bool
    active_turn: bool


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
        store_path = session_store_path or str(default_session_store_path(codex_home))
        self._session_store = ChannelSessionStore(store_path)
        usage_path = usage_store_path or str(default_usage_store_path(codex_home))
        self._usage_store = ChannelUsageStore(usage_path)
        self._discord_worktrees = (
            DiscordWorktreeManager(discord_worktree_root, studyos_org_root)
            if discord_worktree_root
            else None
        )
        try:
            args = shlex.split(command) if command else []
            launch = (
                parse_codex_app_server_command(args)
                if channel_sessions_enabled and is_codex_exec_command(args)
                else None
            )
        except ValueError as exc:
            raise AgentConfigurationError("Agent command configuration is invalid") from exc
        self._codex_runtime = (
            CodexAppServerRuntime(
                CodexAppServerClient(launch.command),
                self._session_store,
                model=launch.model,
                model_provider=launch.model_provider,
                approval_policy=launch.approval_policy,
                sandbox=launch.sandbox,
                turn_timeout_seconds=timeout_seconds,
            )
            if channel_sessions_enabled and launch
            else None
        )
        self._codex_cwd = launch.cwd if launch and launch.cwd else workdir
        self._channel_workspaces: dict[int, Path] = {}

    async def start(self) -> None:
        if self._codex_runtime:
            await self._codex_runtime.start()

    async def close(self) -> None:
        if self._codex_runtime:
            await self._codex_runtime.close()

    async def ask(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None = None,
        attachment_paths: tuple[Path, ...] = (),
        origin_context: DiscordOriginContext | None = None,
        on_progress: ProgressSink | None = None,
        execution: AgentExecutionContext | None = None,
    ) -> AgentReply:
        started_at = time.monotonic()
        logger.info("agent request started source_user=%s channel_id=%s", user, channel_id)
        if execution:
            reply = await self._ask_command(
                prompt,
                user,
                channel_id,
                source_message_id,
                attachment_paths,
                origin_context,
                on_progress,
                execution,
            )
        elif self._webhook_url:
            message, files = await request_agent_webhook(
                self._webhook_url,
                prompt=prompt,
                user=user,
                channel_id=channel_id,
                source_message_id=source_message_id,
                attachment_paths=attachment_paths,
                origin_context=origin_context,
            )
            reply = AgentReply(message=message, files=files)
        elif self._command:
            reply = await self._ask_command(
                prompt,
                user,
                channel_id,
                source_message_id,
                attachment_paths,
                origin_context,
                on_progress,
                execution,
            )
        else:
            raise AgentConfigurationError("Configure an agent webhook or command")

        elapsed = time.monotonic() - started_at
        logger.info(
            "agent request completed source_user=%s channel_id=%s elapsed_seconds=%.2f",
            user,
            channel_id,
            elapsed,
        )
        return reply

    async def _ask_command(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None,
        attachment_paths: tuple[Path, ...],
        origin_context: DiscordOriginContext | None,
        on_progress: ProgressSink | None,
        execution: AgentExecutionContext | None,
    ) -> AgentReply:
        if not self._command:
            raise AgentConfigurationError("Agent command is not configured")

        args = shlex.split(self._command)
        workspace = await self._prepare_discord_workspace(
            args,
            prompt,
            execution.channel_id if execution else None,
        )
        if workspace:
            args = with_codex_cd_args(args, workspace.path)
            if execution:
                self._channel_workspaces[execution.channel_id] = workspace.path
        full_prompt = build_agent_prompt(
            prompt,
            user,
            channel_id,
            os.environ.get("CODEX_HOME"),
            source_message_id,
            tuple(str(path) for path in attachment_paths),
            str(workspace.path) if workspace else None,
            origin_context,
        )
        image_paths = tuple(path for path in attachment_paths if is_image_path(path))
        if execution:
            if self._codex_runtime is None:
                raise AgentConfigurationError("Persistent Discord agent runtime is not configured")
            result = await self._codex_runtime.run(
                channel_id=execution.channel_id,
                prompt=full_prompt,
                cwd=workspace.path if workspace else self._codex_cwd,
                local_images=image_paths,
                on_progress=on_progress,
            )
            command_result = AgentCommandResult(
                message=result.message,
                session_id=result.thread_id,
                usage=result.usage,
            )
            self._record_usage(execution.channel_id, command_result)
            return self._agent_reply_from_result(command_result)

        run_args = add_codex_image_args(args, image_paths) if is_codex_exec_command(args) else args
        result = await run_agent_command(
            run_args,
            full_prompt,
            self._workdir,
            self._timeout_seconds,
        )
        return self._agent_reply_from_result(result)

    async def steer(
        self,
        *,
        prompt: str,
        user: str,
        channel_id: int,
        source_message_id: int | None,
        attachment_paths: tuple[Path, ...] = (),
        origin_context: DiscordOriginContext | None = None,
    ) -> SteerResult:
        if self._codex_runtime is None:
            return SteerResult.NO_ACTIVE_TURN
        workspace = self._channel_workspaces.get(channel_id)
        full_prompt = build_agent_prompt(
            prompt,
            user,
            channel_id,
            os.environ.get("CODEX_HOME"),
            source_message_id,
            tuple(str(path) for path in attachment_paths),
            str(workspace) if workspace else None,
            origin_context,
        )
        images = tuple(path for path in attachment_paths if is_image_path(path))
        return await self._codex_runtime.steer(
            channel_id=channel_id,
            prompt=full_prompt,
            local_images=images,
        )

    async def interrupt(self, channel_id: int) -> bool:
        return await self._codex_runtime.interrupt(channel_id) if self._codex_runtime else False

    async def _prepare_discord_workspace(
        self,
        args: list[str],
        prompt: str,
        channel_id: int | None,
    ) -> DiscordWorkspace | None:
        if (
            self._discord_worktrees is None
            or channel_id is None
            or not is_codex_exec_command(args)
        ):
            return None
        try:
            workspace = await self._discord_worktrees.prepare(prompt, channel_id)
        except (OSError, RuntimeError) as exc:
            raise AgentWorkspaceOrAttachmentError(
                "Discord workspace could not be prepared",
            ) from exc
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
            raise AgentInvalidOutput("Agent command produced an empty artifact response")
        return AgentReply(
            message=parsed.message,
            session_id=result.session_id,
            files=parsed.files,
        )

    def _record_usage(self, channel_id: int, result: AgentCommandResult) -> None:
        self._usage_store.add(channel_id, result.usage, result.session_id)
