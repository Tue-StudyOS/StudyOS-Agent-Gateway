import asyncio
import contextlib
import json
import logging
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from study_discord_agent.codex_app_server_protocol import (
    AppServerClosedError,
    AppServerNotification,
    AppServerProcessError,
    AppServerProtocolError,
    AppServerRpcError,
    JsonObject,
    NotificationHandler,
)

logger = logging.getLogger(__name__)
APP_SERVER_STREAM_LIMIT = 16 * 1024 * 1024


class AppServerTransport:
    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None,
        request_timeout: float,
        shutdown_timeout: float,
    ) -> None:
        if not command:
            raise ValueError("Codex app-server command must not be empty")
        self._command = tuple(command)
        self._env = dict(env) if env is not None else None
        self._request_timeout = request_timeout
        self._shutdown_timeout = shutdown_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._pending: dict[int, asyncio.Future[JsonObject]] = {}
        self._notifications: asyncio.Queue[AppServerNotification] = asyncio.Queue()
        self._handlers: list[NotificationHandler] = []
        self._stderr_tail: deque[str] = deque(maxlen=30)
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._closing = False
        self._failure: BaseException | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._process is not None:
                if self._process.returncode is None:
                    return
                raise AppServerProcessError("Codex app-server process already exited")
            self._closing = False
            self._failure = None
            self._stderr_tail.clear()
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                limit=APP_SERVER_STREAM_LIMIT,
            )
            self._tasks = (
                asyncio.create_task(self._read_stdout()),
                asyncio.create_task(self._read_stderr()),
                asyncio.create_task(self._dispatch_notifications()),
            )

    def subscribe(self, handler: NotificationHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe() -> None:
            self._handlers[:] = [current for current in self._handlers if current is not handler]

        return unsubscribe

    async def request(self, method: str, params: JsonObject) -> JsonObject:
        loop = asyncio.get_running_loop()
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[JsonObject] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            try:
                return await asyncio.wait_for(future, timeout=self._request_timeout)
            except TimeoutError as error:
                raise AppServerProcessError(
                    f"Codex app-server request timed out: {method}"
                ) from error
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: JsonObject) -> None:
        await self._write({"method": method, "params": params})

    async def close(self) -> None:
        async with self._lifecycle_lock:
            process = self._process
            self._closing = True
            self._fail_pending(AppServerClosedError("Codex app-server client closed"))
            if process is not None and process.returncode is None:
                await self._close_stdin(process)
                await self._wait_or_stop(process)
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = ()
            self._process = None
            self._notifications = asyncio.Queue()
            self._failure = None
            self._closing = False

    async def _write(self, message: JsonObject) -> None:
        if self._failure is not None:
            raise AppServerProcessError("Codex app-server transport failed") from self._failure
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerClosedError("Codex app-server process is not running")
        payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            try:
                process.stdin.write(payload)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise AppServerProcessError("Codex app-server stdin closed") from error

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                await self._handle_message(_parse_message(line))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Codex app-server stdout reader failed")
            self._failure = error
            self._fail_pending(error)
            await self._notify_exit(error)
            if process.returncode is None:
                process.terminate()
        else:
            if not self._closing:
                detail = "\n".join(self._stderr_tail).strip()
                suffix = f": {detail[-1000:]}" if detail else ""
                error = AppServerProcessError(f"Codex app-server exited{suffix}")
                self._failure = error
                self._fail_pending(error)
                await self._notify_exit(error)

    async def _handle_message(self, message: JsonObject) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if isinstance(request_id, int) and not isinstance(request_id, bool) and not method:
            self._resolve_response(request_id, message)
            return
        if isinstance(method, str) and request_id is None:
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise AppServerProtocolError("Notification params are not an object")
            await self._notifications.put(AppServerNotification(method=method, params=params))
            return
        if isinstance(method, str) and isinstance(request_id, (int, str)):
            await self._write(
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": "Client method not supported"},
                }
            )
            return
        raise AppServerProtocolError("Unrecognized app-server message")

    def _resolve_response(self, request_id: int, message: JsonObject) -> None:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(_rpc_error(error))
            return
        result = message.get("result")
        if not isinstance(result, dict):
            future.set_exception(AppServerProtocolError("RPC response result is not an object"))
            return
        future.set_result(result)

    async def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while line := await process.stderr.readline():
            self._stderr_tail.append(line.decode(errors="replace").rstrip())

    async def _dispatch_notifications(self) -> None:
        while True:
            notification = await self._notifications.get()
            for handler in tuple(self._handlers):
                try:
                    await handler(notification)
                except Exception:
                    logger.exception("Codex app-server notification handler failed")

    async def _notify_exit(self, error: BaseException) -> None:
        if not self._closing:
            await self._notifications.put(
                AppServerNotification("app-server/exited", {"message": str(error)})
            )

    async def _close_stdin(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None:
            return
        process.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()

    async def _wait_or_stop(self, process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout)
            return
        except TimeoutError:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._shutdown_timeout)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _fail_pending(self, error: BaseException) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)


def _parse_message(line: bytes) -> JsonObject:
    try:
        parsed: object = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppServerProtocolError("Codex app-server emitted invalid JSON") from error
    if not isinstance(parsed, dict):
        raise AppServerProtocolError("Codex app-server message is not an object")
    return cast(JsonObject, parsed)


def _rpc_error(error: JsonObject) -> AppServerRpcError:
    code = error.get("code")
    message = error.get("message")
    return AppServerRpcError(
        code if isinstance(code, int) and not isinstance(code, bool) else None,
        message if isinstance(message, str) else "Unknown RPC error",
        error.get("data"),
    )
