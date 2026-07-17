import asyncio
from collections.abc import Awaitable, Callable

from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import (
    AppServerClosedError,
    AppServerNotification,
)

ClientFactory = Callable[[], CodexAppServerClient]
ConnectionNotificationHandler = Callable[[int, AppServerNotification], Awaitable[None]]


class CodexAppServerConnection:
    """Owns one recoverable Codex app-server client generation."""

    def __init__(
        self,
        factory: ClientFactory,
        on_notification: ConnectionNotificationHandler,
    ) -> None:
        self._factory = factory
        self._on_notification = on_notification
        self._lifecycle_lock = asyncio.Lock()
        self._client: CodexAppServerClient | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._generation = 0
        self._stale = True
        self._closed = False
        self._failure: BaseException | None = None
        self._recovery_task: asyncio.Task[CodexAppServerClient] | None = None

    @property
    def generation(self) -> int:
        return self._generation

    async def start(self) -> CodexAppServerClient:
        async with self._lifecycle_lock:
            if self._closed:
                raise AppServerClosedError("Codex app-server connection is closed")
            if self._client is not None and not self._stale:
                return self._client
            if self._recovery_task is None or self._recovery_task.done():
                self._recovery_task = asyncio.create_task(self._recover())
            recovery = self._recovery_task
        try:
            return await asyncio.shield(recovery)
        except asyncio.CancelledError:
            async with self._lifecycle_lock:
                closed = self._closed
            if closed:
                raise AppServerClosedError("Codex app-server connection is closed") from None
            raise

    async def invalidate(self, generation: int, error: BaseException) -> None:
        async with self._lifecycle_lock:
            if generation == self._generation:
                self._stale = True
                self._failure = error

    async def client_for(self, generation: int) -> CodexAppServerClient | None:
        async with self._lifecycle_lock:
            if generation == self._generation and not self._stale:
                return self._client
            return None

    async def failure_for(self, generation: int) -> BaseException | None:
        async with self._lifecycle_lock:
            if generation == self._generation and self._stale:
                return self._failure
            return None

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._stale = True
            client = self._client
            unsubscribe = self._unsubscribe
            recovery = self._recovery_task
            self._client = None
            self._unsubscribe = None
            self._failure = None
            if recovery is not None and not recovery.done():
                recovery.cancel()
        if unsubscribe:
            unsubscribe()
        if recovery is not None and not recovery.done():
            await asyncio.gather(recovery, return_exceptions=True)
        if client is not None:
            await client.close()

    async def _recover(self) -> CodexAppServerClient:
        async with self._lifecycle_lock:
            if self._closed:
                raise AppServerClosedError("Codex app-server connection is closed")
            self._generation += 1
            generation = self._generation
            old_client = self._client
            old_unsubscribe = self._unsubscribe
            self._client = None
            self._unsubscribe = None
            self._stale = False
            self._failure = None
        if old_unsubscribe:
            old_unsubscribe()
        if old_client is not None:
            await old_client.close()

        client = self._factory()
        try:
            await client.start()
            unsubscribe = client.subscribe(
                lambda notification: self._deliver_notification(generation, notification)
            )
        except BaseException as error:
            await client.close()
            async with self._lifecycle_lock:
                if generation == self._generation:
                    self._stale = True
                    self._failure = error
            raise

        async with self._lifecycle_lock:
            if self._closed or generation != self._generation or self._stale:
                unsubscribe()
                error = (
                    AppServerClosedError("Codex app-server connection is closed")
                    if self._closed
                    else self._failure
                )
            else:
                self._client = client
                self._unsubscribe = unsubscribe
                return client
        await client.close()
        if error is not None:
            raise error
        raise asyncio.CancelledError()

    async def _deliver_notification(
        self,
        generation: int,
        notification: AppServerNotification,
    ) -> None:
        async with self._lifecycle_lock:
            is_current = generation == self._generation and not self._stale
        if is_current:
            await self._on_notification(generation, notification)
