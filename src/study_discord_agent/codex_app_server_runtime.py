import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from study_discord_agent.agent_errors import AgentTurnTimedOut
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_connection import ClientFactory, CodexAppServerConnection
from study_discord_agent.codex_app_server_events import is_not_steerable_error, notification_turn_id
from study_discord_agent.codex_app_server_protocol import (
    ApprovalPolicy,
    AppServerClosedError,
    AppServerNotification,
    AppServerProcessError,
    AppServerProtocolError,
    AppServerRpcError,
    SandboxMode,
)
from study_discord_agent.codex_app_server_runtime_failures import (
    disconnected,
    is_protocol_incompatibility,
    raise_runtime_failure,
)
from study_discord_agent.codex_app_server_thread_loader import load_thread
from study_discord_agent.codex_app_server_turn import (
    ActiveTurn,
    AgentTurnInterrupted,
    AppServerTurnResult,
    ProgressSink,
    SteerResult,
)
from study_discord_agent.codex_app_server_turn_updates import (
    process_notification,
    state_for_notification,
)
from study_discord_agent.session_store import ChannelSessionStore

__all__ = ("AgentTurnInterrupted", "CodexAppServerRuntime", "SteerResult")

class CodexAppServerRuntime:
    def __init__(
        self,
        client: CodexAppServerClient | ClientFactory,
        session_store: ChannelSessionStore,
        *,
        model: str | None = None,
        model_provider: str | None = None,
        approval_policy: ApprovalPolicy | None = None,
        sandbox: SandboxMode | None = None,
        turn_timeout_seconds: float = 900,
    ) -> None:
        factory = client if callable(client) else lambda: client
        self._connection = CodexAppServerConnection(factory, self._on_notification)
        self._session_store = session_store
        self._model = model
        self._model_provider = model_provider
        self._approval_policy: ApprovalPolicy | None = approval_policy
        self._sandbox: SandboxMode | None = sandbox
        self._turn_timeout = turn_timeout_seconds
        self._active: dict[int, ActiveTurn] = {}
        self._active_generations: dict[int, int] = {}
        self._ready: dict[int, asyncio.Event] = {}
        self._starting_threads: dict[int, str] = {}
        self._early_notifications: dict[str, list[AppServerNotification]] = {}
        self._lock = asyncio.Lock()
    async def start(self) -> None:
        await self._start_client()
    async def close(self) -> None:
        await self._connection.close()
        async with self._lock:
            for state in self._active.values():
                if not state.done.done():
                    state.done.set_exception(
                        disconnected(AppServerClosedError("Codex app-server stopped"))
                    )
            self._active.clear()
            self._active_generations.clear()
            for event in self._ready.values():
                event.set()
            self._ready.clear()
            self._starting_threads.clear()
            self._early_notifications.clear()
    async def run(
        self,
        *,
        channel_id: int,
        prompt: str,
        cwd: str | Path | None,
        local_images: Sequence[str | Path] = (),
        on_progress: ProgressSink | None = None,
    ) -> AppServerTurnResult:
        client = await self._start_client()
        generation = self._connection.generation
        ready = asyncio.Event()
        async with self._lock:
            if channel_id in self._active or channel_id in self._ready:
                raise RuntimeError("A Codex turn is already active in this Discord channel")
            self._ready[channel_id] = ready
        try:
            thread_id = await load_thread(
                client,
                self._session_store,
                channel_id,
                cwd,
                model=self._model,
                model_provider=self._model_provider,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
            )
            async with self._lock:
                self._starting_threads[channel_id] = thread_id
            turn = await client.start_turn(thread_id, prompt, local_images=local_images)
            await self._ensure_current_generation(generation)
            loop = asyncio.get_running_loop()
            state = ActiveTurn(
                channel_id=channel_id,
                thread_id=thread_id,
                turn_id=turn.turn_id,
                done=loop.create_future(),
                progress=on_progress,
            )
            async with self._lock:
                current_generation = await self._connection.client_for(generation)
                if current_generation is not None:
                    self._active[channel_id] = state
                    self._active_generations[channel_id] = generation
                    ready.set()
            if current_generation is None:
                await self._ensure_current_generation(generation)
            while True:
                async with self._lock:
                    early = self._early_notifications.pop(thread_id, [])
                    if not early:
                        self._starting_threads.pop(channel_id, None)
                        break
                for notification in early:
                    await process_notification(notification, state)
            return await asyncio.wait_for(asyncio.shield(state.done), timeout=self._turn_timeout)
        except asyncio.CancelledError:
            await self.interrupt(channel_id)
            raise
        except TimeoutError:
            await self.interrupt(channel_id)
            raise AgentTurnTimedOut("Codex app-server turn timed out") from None
        except (
            AppServerClosedError,
            AppServerProcessError,
            AppServerProtocolError,
            AppServerRpcError,
        ) as exc:
            await raise_runtime_failure(self._connection, generation, exc)
        finally:
            async with self._lock:
                self._active.pop(channel_id, None)
                self._active_generations.pop(channel_id, None)
                thread_id = self._starting_threads.pop(channel_id, None)
                if thread_id:
                    self._early_notifications.pop(thread_id, None)
                if event := self._ready.pop(channel_id, None):
                    event.set()
    async def steer(
        self,
        *,
        channel_id: int,
        prompt: str,
        local_images: Sequence[str | Path] = (),
    ) -> SteerResult:
        state = await self._active_turn(channel_id)
        if state is None or state.done.done():
            return SteerResult.NO_ACTIVE_TURN
        client = await self._client_for_active_turn(channel_id, state)
        if client is None:
            return SteerResult.NO_ACTIVE_TURN
        try:
            await client.steer_turn(
                state.thread_id,
                state.turn_id,
                prompt,
                local_images=local_images,
            )
        except AppServerRpcError as exc:
            if is_not_steerable_error(exc):
                return SteerResult.NOT_STEERABLE
            if is_protocol_incompatibility(exc):
                await raise_runtime_failure(self._connection, self._connection.generation, exc)
            raise RuntimeError(f"Codex steering failed: {exc}") from exc
        except (AppServerClosedError, AppServerProcessError, AppServerProtocolError) as exc:
            await raise_runtime_failure(self._connection, self._connection.generation, exc)
        return SteerResult.STEERED
    async def interrupt(self, channel_id: int) -> bool:
        state = await self._active_turn(channel_id)
        if state is None or state.done.done():
            return False
        client = await self._client_for_active_turn(channel_id, state)
        if client is None:
            return False
        try:
            await client.interrupt_turn(state.thread_id, state.turn_id)
        except (
            AppServerClosedError,
            AppServerProcessError,
            AppServerProtocolError,
            AppServerRpcError,
        ) as exc:
            await raise_runtime_failure(self._connection, self._connection.generation, exc)
        return True
    async def _active_turn(self, channel_id: int) -> ActiveTurn | None:
        async with self._lock:
            state = self._active.get(channel_id)
            ready = self._ready.get(channel_id)
        if state or ready is None:
            return state
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
        except TimeoutError:
            return None
        async with self._lock:
            return self._active.get(channel_id)
    async def _on_notification(
        self,
        generation: int,
        notification: AppServerNotification,
    ) -> None:
        if notification.method == "app-server/exited":
            error = notification.error or AppServerProcessError("Codex app-server exited")
            await self._connection.invalidate(generation, error)
            await self._fail_active_turns(error)
            return
        params = cast(dict[str, object], dict(notification.params))
        if await self._buffer_starting_notification(notification, params):
            return
        state = await state_for_notification(self._lock, self._active, params)
        if state is None:
            return
        await process_notification(notification, state)

    async def _fail_active_turns(self, cause: BaseException) -> None:
        async with self._lock:
            states = tuple(self._active.values())
        for state in states:
            if not state.done.done():
                state.done.set_exception(disconnected(cause))

    async def has_active_turn(self, channel_id: int) -> bool:
        async with self._lock:
            return channel_id in self._active

    def has_persisted_session(self, channel_id: int) -> bool:
        return self._session_store.get(channel_id) is not None

    async def _start_client(self) -> CodexAppServerClient:
        try:
            return await self._connection.start()
        except (
            AppServerClosedError,
            AppServerProcessError,
            AppServerProtocolError,
            AppServerRpcError,
        ) as exc:
            await raise_runtime_failure(self._connection, self._connection.generation, exc)

    async def _client_for_active_turn(
        self,
        channel_id: int,
        state: ActiveTurn,
    ) -> CodexAppServerClient | None:
        async with self._lock:
            generation = self._active_generations.get(channel_id)
        if generation is None:
            return None
        client = await self._connection.client_for(generation)
        if client is None and not state.done.done():
            state.done.set_exception(
                disconnected(AppServerClosedError("Codex app-server disconnected"))
            )
        return client

    async def _ensure_current_generation(self, generation: int) -> None:
        if await self._connection.client_for(generation) is not None:
            return
        error = await self._connection.failure_for(generation)
        await raise_runtime_failure(
            self._connection,
            generation,
            error or AppServerClosedError("Codex app-server disconnected"),
        )

    async def _buffer_starting_notification(
        self,
        notification: AppServerNotification,
        params: Mapping[str, object],
    ) -> bool:
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str) or not notification_turn_id(params):
            return False
        async with self._lock:
            if thread_id in self._starting_threads.values():
                self._early_notifications.setdefault(thread_id, []).append(notification)
                return True
        return False
