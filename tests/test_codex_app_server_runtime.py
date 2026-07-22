import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from study_discord_agent.agent_errors import (
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
    AgentTurnTimedOut,
)
from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_connection import CodexAppServerConnection
from study_discord_agent.codex_app_server_protocol import (
    AppServerNotification,
    AppServerProcessError,
    AppServerProtocolError,
    AppServerRpcError,
    JsonObject,
    NotificationHandler,
    ThreadRef,
    TurnRef,
)
from study_discord_agent.codex_app_server_runtime import (
    AgentTurnInterrupted,
    CodexAppServerRuntime,
    SteerResult,
)
from study_discord_agent.session_store import ChannelSessionStore


class FakeAppServerClient:
    def __init__(self) -> None:
        self.handlers: list[NotificationHandler] = []
        self.started = 0
        self.started_threads: list[str | Path | None] = []
        self.resumed_threads: list[str] = []
        self.started_turns: list[tuple[str, str, tuple[str | Path, ...]]] = []
        self.steered_turns: list[tuple[str, str, str]] = []
        self.interrupted_turns: list[tuple[str, str]] = []
        self.turn_started = asyncio.Event()
        self.complete_during_start = False
        self.exit_during_start = False
        self.close_calls = 0
        self.start_error: BaseException | None = None
        self.resume_error: BaseException | None = None
        self.steer_error: BaseException | None = None
        self.interrupt_error: BaseException | None = None
        self.steer_entered = asyncio.Event()
        self.interrupt_entered = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.release_steer = asyncio.Event()
        self.release_interrupt = asyncio.Event()
        self.release_close = asyncio.Event()
        self.block_steer = False
        self.block_interrupt = False
        self.block_close = False
        self.block_thread_start = False
        self.block_client_start = False
        self.thread_start_entered = asyncio.Event()
        self.client_start_entered = asyncio.Event()
        self.subscribed = asyncio.Event()
        self.release_thread_start = asyncio.Event()
        self.release_client_start = asyncio.Event()
        self.unsubscribe_calls = 0
        self.cancel_recovery_on_subscribe: Callable[[], None] | None = None

    async def start(self) -> object:
        self.client_start_entered.set()
        if self.block_client_start:
            await self.release_client_start.wait()
        if self.start_error:
            raise self.start_error
        self.started += 1
        return object()

    def subscribe(self, handler: NotificationHandler) -> Callable[[], None]:
        self.handlers.append(handler)
        self.subscribed.set()
        if self.cancel_recovery_on_subscribe:
            self.cancel_recovery_on_subscribe()

        def unsubscribe() -> None:
            self.unsubscribe_calls += 1
            self.handlers.remove(handler)

        return unsubscribe

    async def start_thread(self, *, cwd: str | Path | None = None, **_: object) -> ThreadRef:
        self.started_threads.append(cwd)
        self.thread_start_entered.set()
        if self.block_thread_start:
            await self.release_thread_start.wait()
        return ThreadRef(f"thread-{len(self.started_threads)}")

    async def resume_thread(self, thread_id: str, **_: object) -> ThreadRef:
        self.resumed_threads.append(thread_id)
        if self.resume_error:
            raise self.resume_error
        return ThreadRef(thread_id)

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        local_images: Sequence[str | Path] = (),
        **_: object,
    ) -> TurnRef:
        self.started_turns.append((thread_id, prompt, tuple(local_images)))
        self.turn_started.set()
        if self.exit_during_start:
            await self.emit_exit(AppServerProcessError("process exited"))
        if self.complete_during_start:
            await self.emit(
                "item/completed",
                {
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "fast result",
                    },
                },
            )
            await self.emit(
                "turn/completed",
                {"threadId": thread_id, "turn": {"id": "turn-1", "status": "completed"}},
            )
        return TurnRef(thread_id, "turn-1")

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        prompt: str,
        **_: object,
    ) -> TurnRef:
        self.steered_turns.append((thread_id, turn_id, prompt))
        self.steer_entered.set()
        if self.block_steer:
            await self.release_steer.wait()
        if self.steer_error:
            raise self.steer_error
        return TurnRef(thread_id, turn_id)

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        self.interrupted_turns.append((thread_id, turn_id))
        self.interrupt_entered.set()
        if self.block_interrupt:
            await self.release_interrupt.wait()
        if self.interrupt_error:
            raise self.interrupt_error

    async def close(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        if self.block_close:
            await self.release_close.wait()

    async def emit(self, method: str, params: JsonObject) -> None:
        notification = AppServerNotification(method, params)
        for handler in tuple(self.handlers):
            await handler(notification)

    async def emit_exit(self, error: BaseException | None = None) -> None:
        notification = AppServerNotification("app-server/exited", {}, error)
        for handler in tuple(self.handlers):
            await handler(notification)


class FakeClientFactory:
    def __init__(self, clients: list[FakeAppServerClient]) -> None:
        self._clients = clients
        self.calls = 0

    def __call__(self) -> CodexAppServerClient:
        client = self._clients[self.calls]
        self.calls += 1
        return cast(CodexAppServerClient, client)


def _runtime(tmp_path: Path, client: FakeAppServerClient) -> CodexAppServerRuntime:
    return CodexAppServerRuntime(
        cast(CodexAppServerClient, client),
        ChannelSessionStore(tmp_path / "sessions.json"),
        turn_timeout_seconds=2,
    )


def _factory_runtime(tmp_path: Path, factory: FakeClientFactory) -> CodexAppServerRuntime:
    return CodexAppServerRuntime(
        factory,
        ChannelSessionStore(tmp_path / "sessions.json"),
        turn_timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_fast_turn_notifications_are_replayed_after_registration(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.complete_during_start = True
    runtime = _runtime(tmp_path, client)

    result = await runtime.run(channel_id=123, prompt="fast", cwd=tmp_path)

    assert result.message == "fast result"


async def _wait_active(client: FakeAppServerClient) -> None:
    await client.turn_started.wait()
    await asyncio.sleep(0)


async def _complete(client: FakeAppServerClient, thread_id: str, text: str) -> None:
    await client.emit(
        "item/completed",
        {
            "threadId": thread_id,
            "turnId": "turn-1",
            "item": {"type": "agentMessage", "phase": "final_answer", "text": text},
        },
    )
    await client.emit(
        "turn/completed",
        {"threadId": thread_id, "turn": {"id": "turn-1", "status": "completed"}},
    )


@pytest.mark.asyncio
async def test_run_streams_progress_and_returns_final_answer(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    progress: list[str] = []

    async def capture(update: object) -> None:
        progress.append(str(getattr(update, "now", None)))

    task = asyncio.create_task(
        runtime.run(channel_id=123, prompt="hello", cwd=tmp_path, on_progress=capture)
    )
    await _wait_active(client)
    await client.emit(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "agentMessage", "phase": "final_answer", "text": "done"},
        },
    )
    await client.emit(
        "thread/tokenUsage/updated",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "tokenUsage": {
                "last": {
                    "inputTokens": 10,
                    "cachedInputTokens": 2,
                    "outputTokens": 3,
                    "reasoningOutputTokens": 1,
                }
            },
        },
    )
    await client.emit(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
    )

    result = await task

    assert result.message == "done"
    assert result.usage.total_tokens == 13
    assert ChannelSessionStore(tmp_path / "sessions.json").get(123) == "thread-1"
    assert client.started_threads == [tmp_path]
    assert len(client.started_turns) == 1
    assert progress == []


@pytest.mark.asyncio
async def test_followup_steers_the_same_active_turn(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="first", cwd=tmp_path))
    await _wait_active(client)

    result = await runtime.steer(channel_id=123, prompt="new direction")

    assert result is SteerResult.STEERED
    assert client.steered_turns == [("thread-1", "turn-1", "new direction")]
    assert len(client.started_turns) == 1
    await client.emit(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "agentMessage", "phase": "final_answer", "text": "steered"},
        },
    )
    await client.emit(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
    )
    assert (await task).message == "steered"


@pytest.mark.asyncio
async def test_steer_preserves_non_protocol_rpc_error(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    rpc_error = AppServerRpcError(401, "Unauthorized")
    client.steer_error = rpc_error
    runtime = _runtime(tmp_path, client)
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="first", cwd=tmp_path))
    await _wait_active(client)

    with pytest.raises(AppServerRpcError) as exc_info:
        await runtime.steer(channel_id=123, prompt="later")

    assert exc_info.value is rpc_error
    await _complete(client, "thread-1", "done")
    assert (await task).message == "done"


@pytest.mark.asyncio
async def test_interrupt_uses_active_turn_and_resolves_run(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="first", cwd=tmp_path))
    await _wait_active(client)

    assert await runtime.interrupt(123)
    assert client.interrupted_turns == [("thread-1", "turn-1")]
    await client.emit(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted"}},
    )
    with pytest.raises(AgentTurnInterrupted):
        await task


@pytest.mark.asyncio
async def test_turn_timeout_interrupts_and_raises_typed_error(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = CodexAppServerRuntime(
        cast(CodexAppServerClient, client),
        ChannelSessionStore(tmp_path / "sessions.json"),
        turn_timeout_seconds=0.01,
        interrupt_grace_seconds=0,
    )

    with pytest.raises(AgentTurnTimedOut):
        await runtime.run(channel_id=123, prompt="first", cwd=tmp_path)

    assert client.interrupted_turns == [("thread-1", "turn-1")]


@pytest.mark.asyncio
async def test_turn_timeout_waits_for_interrupt_completion(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = CodexAppServerRuntime(
        cast(CodexAppServerClient, client),
        ChannelSessionStore(tmp_path / "sessions.json"),
        turn_timeout_seconds=0.01,
        interrupt_grace_seconds=0.2,
    )
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="slow", cwd=tmp_path))
    await _wait_active(client)
    for _ in range(100):
        if client.interrupted_turns:
            break
        await asyncio.sleep(0.005)

    assert client.interrupted_turns == [("thread-1", "turn-1")]
    assert not task.done()
    await client.emit(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted"}},
    )

    with pytest.raises(AgentTurnTimedOut, match="0.01 seconds"):
        await task


@pytest.mark.asyncio
async def test_existing_channel_thread_is_resumed(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    store = ChannelSessionStore(tmp_path / "sessions.json")
    store.set(123, "stored-thread")
    runtime = CodexAppServerRuntime(cast(CodexAppServerClient, client), store)
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="again", cwd=tmp_path))
    await _wait_active(client)

    assert client.resumed_threads == ["stored-thread"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.interrupted_turns == [("stored-thread", "turn-1")]


@pytest.mark.asyncio
async def test_different_channels_run_concurrently(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    first = asyncio.create_task(runtime.run(channel_id=101, prompt="one", cwd=tmp_path))
    second = asyncio.create_task(runtime.run(channel_id=202, prompt="two", cwd=tmp_path))
    for _ in range(100):
        if len(client.started_turns) == 2:
            break
        await asyncio.sleep(0.01)

    assert len(client.started_turns) == 2
    await _complete(client, "thread-1", "one done")
    await _complete(client, "thread-2", "two done")

    assert (await first).message == "one done"
    assert (await second).message == "two done"


@pytest.mark.asyncio
async def test_server_exit_fails_active_turn_immediately(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    task = asyncio.create_task(runtime.run(channel_id=123, prompt="one", cwd=tmp_path))
    await _wait_active(client)

    exit_error = AppServerProcessError("process exited")
    await client.emit_exit(exit_error)

    with pytest.raises(AgentRuntimeDisconnected) as exc_info:
        await task
    assert exc_info.value.__cause__ is exit_error


@pytest.mark.asyncio
async def test_exit_during_turn_start_fails_without_waiting_for_timeout(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.exit_during_start = True
    runtime = _runtime(tmp_path, client)

    with pytest.raises(AgentRuntimeDisconnected):
        await asyncio.wait_for(runtime.run(channel_id=1, prompt="one", cwd=tmp_path), timeout=0.1)


@pytest.mark.asyncio
async def test_exit_does_not_recover_to_steer_or_interrupt_the_old_turn(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    replacement = FakeAppServerClient()
    factory = FakeClientFactory([first_client, replacement])
    runtime = _factory_runtime(tmp_path, factory)
    task = asyncio.create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    await _wait_active(first_client)

    await first_client.emit_exit(AppServerProcessError("process exited"))

    assert await runtime.steer(channel_id=1, prompt="later") is SteerResult.NO_ACTIVE_TURN
    assert not await runtime.interrupt(1)
    assert factory.calls == 1
    with pytest.raises(AgentRuntimeDisconnected):
        await task


@pytest.mark.asyncio
async def test_delayed_steer_failure_does_not_invalidate_replacement(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    first_client.block_steer = True
    first_client.steer_error = AppServerProcessError("old steer failed")
    replacement = FakeAppServerClient()
    unexpected = FakeAppServerClient()
    factory = FakeClientFactory([first_client, replacement, unexpected])
    runtime = _factory_runtime(tmp_path, factory)
    turn = asyncio.create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    await _wait_active(first_client)

    steer = asyncio.create_task(runtime.steer(channel_id=1, prompt="later"))
    await first_client.steer_entered.wait()
    await first_client.emit_exit(AppServerProcessError("process exited"))
    with pytest.raises(AgentRuntimeDisconnected):
        await turn
    await runtime.start()

    first_client.release_steer.set()
    with pytest.raises(AgentRuntimeDisconnected):
        await steer

    await runtime.start()
    assert factory.calls == 2
    assert replacement.close_calls == 0


@pytest.mark.asyncio
async def test_delayed_interrupt_failure_does_not_invalidate_replacement(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    first_client.block_interrupt = True
    first_client.interrupt_error = AppServerProcessError("old interrupt failed")
    replacement = FakeAppServerClient()
    unexpected = FakeAppServerClient()
    factory = FakeClientFactory([first_client, replacement, unexpected])
    runtime = _factory_runtime(tmp_path, factory)
    turn = asyncio.create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    await _wait_active(first_client)

    interrupt = asyncio.create_task(runtime.interrupt(1))
    await first_client.interrupt_entered.wait()
    await first_client.emit_exit(AppServerProcessError("process exited"))
    with pytest.raises(AgentRuntimeDisconnected):
        await turn
    await runtime.start()

    first_client.release_interrupt.set()
    with pytest.raises(AgentRuntimeDisconnected):
        await interrupt

    await runtime.start()
    assert factory.calls == 2
    assert replacement.close_calls == 0


@pytest.mark.asyncio
async def test_close_is_terminal_during_and_after_old_client_shutdown(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    first_client.block_close = True
    replacement = FakeAppServerClient()
    factory = FakeClientFactory([first_client, replacement])
    runtime = _factory_runtime(tmp_path, factory)
    await runtime.start()

    closing = asyncio.create_task(runtime.close())
    await first_client.close_entered.wait()

    with pytest.raises(AgentRuntimeDisconnected):
        await runtime.start()
    with pytest.raises(AgentRuntimeDisconnected):
        await runtime.run(channel_id=1, prompt="new", cwd=tmp_path)
    assert factory.calls == 1
    assert replacement.started == 0
    assert replacement.started_turns == []

    first_client.release_close.set()
    await closing

    with pytest.raises(AgentRuntimeDisconnected):
        await runtime.start()
    assert factory.calls == 1


@pytest.mark.asyncio
async def test_close_during_thread_load_never_starts_turn(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.block_thread_start = True
    runtime = _runtime(tmp_path, client)
    run = asyncio.create_task(runtime.run(channel_id=1, prompt="new", cwd=tmp_path))
    await client.thread_start_entered.wait()

    await runtime.close()
    client.release_thread_start.set()

    with pytest.raises(AgentRuntimeDisconnected):
        await run
    assert client.started_turns == []


@pytest.mark.asyncio
async def test_cancelled_unpublished_recovery_retires_client_and_subscription() -> None:
    client = FakeAppServerClient()
    client.block_client_start = True

    async def ignore_notification(_: int, __: AppServerNotification) -> None:
        return None

    connection = CodexAppServerConnection(
        lambda: cast(CodexAppServerClient, client),
        ignore_notification,
    )
    start = asyncio.create_task(connection.start())
    await client.client_start_entered.wait()
    lifecycle_lock = cast(Any, connection)._lifecycle_lock
    await lifecycle_lock.acquire()
    client.cancel_recovery_on_subscribe = cast(Any, connection)._recovery_task.cancel
    client.release_client_start.set()
    await client.subscribed.wait()
    lifecycle_lock.release()

    with pytest.raises(asyncio.CancelledError):
        await start

    assert client.close_calls == 1
    assert client.unsubscribe_calls == 1


@pytest.mark.asyncio
async def test_close_clears_active_generation_state(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    runtime = _runtime(tmp_path, client)
    task = asyncio.create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    await _wait_active(client)

    await runtime.close()

    assert not await runtime.has_active_turn(1)
    with pytest.raises(AgentRuntimeDisconnected):
        await task


@pytest.mark.asyncio
async def test_exit_fails_all_turns_and_concurrent_retry_uses_one_client(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    replacement = FakeAppServerClient()
    replacement.complete_during_start = True
    factory = FakeClientFactory([first_client, replacement])
    runtime = _factory_runtime(tmp_path, factory)

    first = asyncio.create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    second = asyncio.create_task(runtime.run(channel_id=2, prompt="two", cwd=tmp_path))
    for _ in range(100):
        if len(first_client.started_turns) == 2:
            break
        await asyncio.sleep(0.01)

    await first_client.emit_exit()

    with pytest.raises(AgentRuntimeDisconnected):
        await first
    with pytest.raises(AgentRuntimeDisconnected):
        await second

    resumed = await asyncio.gather(
        runtime.run(channel_id=1, prompt="retry", cwd=tmp_path),
        runtime.run(channel_id=2, prompt="retry", cwd=tmp_path),
    )

    assert [result.message for result in resumed] == ["fast result", "fast result"]
    assert factory.calls == 2
    assert sorted(replacement.resumed_threads) == ["thread-1", "thread-2"]
    assert first_client.close_calls == 1


@pytest.mark.asyncio
async def test_stale_generation_notifications_do_not_complete_recovered_turn(
    tmp_path: Path,
) -> None:
    first_client = FakeAppServerClient()
    first_client.complete_during_start = True
    replacement = FakeAppServerClient()
    factory = FakeClientFactory([first_client, replacement])
    runtime = _factory_runtime(tmp_path, factory)

    await runtime.run(channel_id=1, prompt="first", cwd=tmp_path)
    stale_handler = first_client.handlers[0]
    await first_client.emit_exit(AppServerProcessError("process exited"))
    retry = asyncio.create_task(runtime.run(channel_id=1, prompt="retry", cwd=tmp_path))
    await _wait_active(replacement)

    await stale_handler(
        AppServerNotification(
            "turn/completed",
            {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
        )
    )

    assert not retry.done()
    await _complete(replacement, "thread-1", "replacement result")
    assert (await retry).message == "replacement result"


@pytest.mark.asyncio
async def test_failed_recovery_never_starts_one_shot_or_new_thread(tmp_path: Path) -> None:
    first_client = FakeAppServerClient()
    first_client.complete_during_start = True
    replacement = FakeAppServerClient()
    replacement.resume_error = AppServerProcessError("connection lost")
    factory = FakeClientFactory([first_client, replacement])
    runtime = _factory_runtime(tmp_path, factory)

    await runtime.run(channel_id=1, prompt="first", cwd=tmp_path)
    await first_client.emit_exit()

    with pytest.raises(AgentRuntimeDisconnected):
        await runtime.run(channel_id=1, prompt="retry", cwd=tmp_path)

    assert replacement.resumed_threads == ["thread-1"]
    assert replacement.started_threads == []
    assert replacement.started_turns == []


@pytest.mark.asyncio
async def test_initialize_protocol_mismatch_is_incompatible(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.start_error = AppServerProtocolError("unexpected initialize result")
    runtime = _runtime(tmp_path, client)

    with pytest.raises(AgentRuntimeIncompatible):
        await runtime.start()


@pytest.mark.asyncio
async def test_initialize_method_mismatch_is_incompatible(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.start_error = AppServerRpcError(-32601, "Method not found")
    runtime = _runtime(tmp_path, client)

    with pytest.raises(AgentRuntimeIncompatible):
        await runtime.start()


@pytest.mark.asyncio
async def test_initialize_service_error_is_not_protocol_incompatibility(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.start_error = AppServerRpcError(401, "Unauthorized")
    runtime = _runtime(tmp_path, client)

    with pytest.raises(AppServerRpcError, match="Unauthorized"):
        await runtime.start()


@pytest.mark.asyncio
async def test_resume_protocol_error_is_incompatible_without_starting_turn(tmp_path: Path) -> None:
    client = FakeAppServerClient()
    client.resume_error = AppServerProtocolError("missing thread id")
    store = ChannelSessionStore(tmp_path / "sessions.json")
    store.set(1, "stored-thread")
    runtime = CodexAppServerRuntime(cast(CodexAppServerClient, client), store)

    with pytest.raises(AgentRuntimeIncompatible):
        await runtime.run(channel_id=1, prompt="retry", cwd=tmp_path)

    assert client.started_threads == []
    assert client.started_turns == []
