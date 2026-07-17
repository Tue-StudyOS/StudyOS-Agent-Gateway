from typing import NoReturn

from study_discord_agent.agent_errors import (
    AgentRuntimeDisconnected,
    AgentRuntimeIncompatible,
)
from study_discord_agent.codex_app_server_connection import CodexAppServerConnection
from study_discord_agent.codex_app_server_protocol import (
    AppServerClosedError,
    AppServerProcessError,
    AppServerProtocolError,
    AppServerRpcError,
)


async def raise_runtime_failure(
    connection: CodexAppServerConnection,
    generation: int,
    error: BaseException,
) -> NoReturn:
    if is_protocol_incompatibility(error):
        await connection.invalidate(generation, error)
        raise AgentRuntimeIncompatible("Codex app-server protocol is incompatible") from error
    if isinstance(error, (AppServerClosedError, AppServerProcessError)):
        await connection.invalidate(generation, error)
        raise disconnected(error)
    raise error


def is_protocol_incompatibility(error: BaseException) -> bool:
    return isinstance(error, AppServerProtocolError) or (
        isinstance(error, AppServerRpcError) and error.code in {-32601, -32602}
    )


def disconnected(cause: BaseException) -> AgentRuntimeDisconnected:
    error = AgentRuntimeDisconnected("Codex app-server disconnected")
    error.__cause__ = cause
    return error
