import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from study_discord_agent.agent_progress import progress_from_notification
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_events import (
    agent_message,
    is_not_steerable_error,
    notification_turn_id,
    turn_error_message,
    usage_from_notification,
)
from study_discord_agent.codex_app_server_protocol import (
    ApprovalPolicy,
    AppServerNotification,
    AppServerRpcError,
    SandboxMode,
)
from study_discord_agent.codex_app_server_turn import (
    ActiveTurn,
    AgentTurnInterrupted,
    AppServerTurnResult,
    ProgressSink,
    SteerResult,
)
from study_discord_agent.session_store import ChannelSessionStore

logger = logging.getLogger(__name__)


class CodexAppServerRuntime:
    def __init__(
        self,
        client: CodexAppServerClient,
        session_store: ChannelSessionStore,
        *,
        model: str | None = None,
        model_provider: str | None = None,
        approval_policy: ApprovalPolicy | None = None,
        sandbox: SandboxMode | None = None,
        turn_timeout_seconds: float = 900,
    ) -> None:
        self._client = client
        self._session_store = session_store
        self._model = model
        self._model_provider = model_provider
        self._approval_policy: ApprovalPolicy | None = approval_policy
        self._sandbox: SandboxMode | None = sandbox
        self._turn_timeout = turn_timeout_seconds
        self._active: dict[int, ActiveTurn] = {}
        self._ready: dict[int, asyncio.Event] = {}
        self._starting_threads: dict[int, str] = {}
        self._early_notifications: dict[str, list[AppServerNotification]] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            await self._client.start()
            self._unsubscribe = self._client.subscribe(self._on_notification)
            self._started = True

    async def close(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        await self._client.close()
        async with self._lock:
            self._started = False
            for state in self._active.values():
                if not state.done.done():
                    state.done.set_exception(RuntimeError("Codex app-server stopped"))
            self._active.clear()
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
        await self.start()
        ready = asyncio.Event()
        async with self._lock:
            if channel_id in self._active or channel_id in self._ready:
                raise RuntimeError("A Codex turn is already active in this Discord channel")
            self._ready[channel_id] = ready
        try:
            thread_id = await self._load_thread(channel_id, cwd)
            async with self._lock:
                self._starting_threads[channel_id] = thread_id
            turn = await self._client.start_turn(thread_id, prompt, local_images=local_images)
            loop = asyncio.get_running_loop()
            state = ActiveTurn(
                channel_id=channel_id,
                thread_id=thread_id,
                turn_id=turn.turn_id,
                done=loop.create_future(),
                progress=on_progress,
            )
            async with self._lock:
                self._active[channel_id] = state
                ready.set()
            while True:
                async with self._lock:
                    early = self._early_notifications.pop(thread_id, [])
                    if not early:
                        self._starting_threads.pop(channel_id, None)
                        break
                for notification in early:
                    await self._process_notification(notification)
            return await asyncio.wait_for(asyncio.shield(state.done), timeout=self._turn_timeout)
        except asyncio.CancelledError:
            await self.interrupt(channel_id)
            raise
        except TimeoutError:
            await self.interrupt(channel_id)
            raise RuntimeError("Codex app-server turn timed out") from None
        finally:
            async with self._lock:
                self._active.pop(channel_id, None)
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
        try:
            await self._client.steer_turn(
                state.thread_id,
                state.turn_id,
                prompt,
                local_images=local_images,
            )
        except AppServerRpcError as exc:
            if is_not_steerable_error(exc):
                return SteerResult.NOT_STEERABLE
            raise RuntimeError(f"Codex steering failed: {exc}") from exc
        return SteerResult.STEERED

    async def interrupt(self, channel_id: int) -> bool:
        state = await self._active_turn(channel_id)
        if state is None or state.done.done():
            return False
        await self._client.interrupt_turn(state.thread_id, state.turn_id)
        return True

    async def _load_thread(self, channel_id: int, cwd: str | Path | None) -> str:
        existing = self._session_store.get(channel_id)
        thread = (
            await self._client.resume_thread(
                existing,
                cwd=cwd,
                model=self._model,
                model_provider=self._model_provider,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
            )
            if existing
            else await self._client.start_thread(
                cwd=cwd,
                model=self._model,
                model_provider=self._model_provider,
                approval_policy=self._approval_policy,
                sandbox=self._sandbox,
            )
        )
        self._session_store.set(channel_id, thread.thread_id)
        return thread.thread_id

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

    async def _on_notification(self, notification: AppServerNotification) -> None:
        if notification.method == "app-server/exited":
            await self._fail_active_turns(notification.params.get("message"))
            return
        params = cast(dict[str, object], dict(notification.params))
        if await self._buffer_starting_notification(notification, params):
            return
        await self._process_notification(notification)

    async def _process_notification(self, notification: AppServerNotification) -> None:
        params = cast(dict[str, object], dict(notification.params))
        state = await self._state_for_notification(params)
        if state is None:
            return
        if notification.method == "item/completed":
            if message := agent_message(params):
                phase, text = message
                if phase == "final_answer":
                    state.final_message = text
                elif phase is None:
                    state.fallback_message = text
        elif notification.method == "thread/tokenUsage/updated":
            state.usage = usage_from_notification(params)
        if (progress := progress_from_notification(notification.method, params)) and state.progress:
            await state.progress(progress)
        if notification.method == "turn/completed":
            self._complete_turn(state, params)

    async def _fail_active_turns(self, message: object) -> None:
        error = str(message) if isinstance(message, str) else "Codex app-server exited"
        async with self._lock:
            states = tuple(self._active.values())
        for state in states:
            if not state.done.done():
                state.done.set_exception(RuntimeError(error))

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

    async def _state_for_notification(
        self,
        params: Mapping[str, object],
    ) -> ActiveTurn | None:
        thread_id = params.get("threadId")
        turn_id = notification_turn_id(params)
        async with self._lock:
            return next(
                (
                    state
                    for state in self._active.values()
                    if state.thread_id == thread_id and state.turn_id == turn_id
                ),
                None,
            )

    def _complete_turn(self, state: ActiveTurn, params: Mapping[str, object]) -> None:
        if state.done.done():
            return
        turn_obj = params.get("turn")
        turn = cast(dict[str, object], turn_obj) if isinstance(turn_obj, dict) else {}
        status = turn.get("status")
        if status == "interrupted":
            state.done.set_exception(AgentTurnInterrupted("Codex turn was interrupted"))
            return
        if status == "failed":
            state.done.set_exception(RuntimeError(turn_error_message(turn.get("error"))))
            return
        message = state.final_message or state.fallback_message
        if not message:
            state.done.set_exception(RuntimeError("Codex app-server produced no final response"))
            return
        state.done.set_result(
            AppServerTurnResult(
                message=message.strip(),
                thread_id=state.thread_id,
                usage=state.usage,
            )
        )
