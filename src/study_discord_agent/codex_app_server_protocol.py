from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

type ApprovalPolicy = Literal["untrusted", "on-request", "never"]
type SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]


@dataclass(frozen=True)
class InitializeResult:
    user_agent: str
    platform_family: str
    platform_os: str
    codex_home: str


@dataclass(frozen=True)
class ThreadRef:
    thread_id: str


@dataclass(frozen=True)
class TurnRef:
    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class AppServerNotification:
    method: str
    params: Mapping[str, JsonValue]
    error: BaseException | None = None


class NotificationHandler(Protocol):
    def __call__(self, notification: AppServerNotification, /) -> Awaitable[None]: ...


class AppServerError(RuntimeError):
    """Base error raised by the Codex app-server client."""


class AppServerClosedError(AppServerError):
    """Raised when an operation requires a running app-server connection."""


class AppServerProcessError(AppServerError):
    """Raised when the app-server process exits or its transport fails."""


class AppServerProtocolError(AppServerError):
    """Raised when the app-server emits an invalid or unexpected message."""


class AppServerRpcError(AppServerError):
    def __init__(self, code: int | None, message: str, data: JsonValue = None) -> None:
        super().__init__(f"Codex app-server RPC failed ({code}): {message}")
        self.code = code
        self.message = message
        self.data = data
