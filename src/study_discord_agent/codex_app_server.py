import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from study_discord_agent.codex_app_server_protocol import (
    ApprovalPolicy,
    AppServerClosedError,
    AppServerProtocolError,
    InitializeResult,
    JsonObject,
    JsonValue,
    NotificationHandler,
    SandboxMode,
    ThreadRef,
    TurnRef,
)
from study_discord_agent.codex_app_server_transport import AppServerTransport


class CodexAppServerClient:
    """Persistent JSONL client for the Codex app-server V2 thread/turn API."""

    def __init__(
        self,
        command: Sequence[str] = ("codex", "app-server", "--listen", "stdio://"),
        *,
        env: Mapping[str, str] | None = None,
        request_timeout: float = 30.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        self._transport = AppServerTransport(
            command,
            env=env,
            request_timeout=request_timeout,
            shutdown_timeout=shutdown_timeout,
        )
        self._initialize_result: InitializeResult | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def __aenter__(self) -> "CodexAppServerClient":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self) -> InitializeResult:
        async with self._lifecycle_lock:
            if self._initialize_result is not None:
                return self._initialize_result
            await self._transport.start()
            try:
                result = await self._transport.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "studyos_agent_gateway",
                            "title": "StudyOS Agent Gateway",
                            "version": "0.1.0",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                )
                self._initialize_result = _parse_initialize_result(result)
                await self._transport.notify("initialized", {})
            except BaseException:
                await self._transport.close()
                raise
            return self._initialize_result

    def subscribe(self, handler: NotificationHandler) -> Callable[[], None]:
        return self._transport.subscribe(handler)

    async def start_thread(
        self,
        *,
        cwd: str | Path | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        approval_policy: ApprovalPolicy | None = None,
        sandbox: SandboxMode | None = None,
        config: Mapping[str, JsonValue] | None = None,
        developer_instructions: str | None = None,
        dynamic_tools: Sequence[JsonObject] | None = None,
        environments: Sequence[JsonObject] | None = None,
        runtime_workspace_roots: Sequence[str | Path] | None = None,
    ) -> ThreadRef:
        params = _thread_params(
            cwd,
            model,
            model_provider,
            approval_policy,
            sandbox,
            config,
            developer_instructions,
            dynamic_tools,
            environments,
            runtime_workspace_roots,
        )
        return _parse_thread(await self._request("thread/start", params))

    async def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str | Path | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        approval_policy: ApprovalPolicy | None = None,
        sandbox: SandboxMode | None = None,
        config: Mapping[str, JsonValue] | None = None,
        developer_instructions: str | None = None,
        runtime_workspace_roots: Sequence[str | Path] | None = None,
    ) -> ThreadRef:
        params = _thread_params(
            cwd,
            model,
            model_provider,
            approval_policy,
            sandbox,
            config,
            developer_instructions,
            None,
            None,
            runtime_workspace_roots,
        )
        params["threadId"] = _nonempty(thread_id, "thread id")
        return _parse_thread(await self._request("thread/resume", params))

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        local_images: Sequence[str | Path] = (),
        approval_policy: ApprovalPolicy | None = None,
        sandbox_policy: JsonObject | None = None,
        environments: Sequence[JsonObject] | None = None,
        cwd: str | Path | None = None,
        runtime_workspace_roots: Sequence[str | Path] | None = None,
    ) -> TurnRef:
        thread_id = _nonempty(thread_id, "thread id")
        result = await self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": _user_input(prompt, local_images),
                **({"approvalPolicy": approval_policy} if approval_policy else {}),
                **({"sandboxPolicy": sandbox_policy} if sandbox_policy else {}),
                **(
                    {"environments": list(environments)}
                    if environments is not None
                    else {}
                ),
                **({"cwd": str(cwd)} if cwd is not None else {}),
                **(
                    {
                        "runtimeWorkspaceRoots": [
                            str(root) for root in runtime_workspace_roots
                        ]
                    }
                    if runtime_workspace_roots is not None
                    else {}
                ),
            },
        )
        return TurnRef(
            thread_id=thread_id,
            turn_id=_string_field(_object_field(result, "turn"), "id"),
        )

    async def steer_turn(
        self,
        thread_id: str,
        turn_id: str,
        prompt: str,
        *,
        local_images: Sequence[str | Path] = (),
    ) -> TurnRef:
        thread_id = _nonempty(thread_id, "thread id")
        turn_id = _nonempty(turn_id, "turn id")
        result = await self._request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": _user_input(prompt, local_images),
            },
        )
        return TurnRef(thread_id=thread_id, turn_id=_string_field(result, "turnId"))

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        await self._request(
            "turn/interrupt",
            {
                "threadId": _nonempty(thread_id, "thread id"),
                "turnId": _nonempty(turn_id, "turn id"),
            },
        )

    async def close(self) -> None:
        async with self._lifecycle_lock:
            self._initialize_result = None
            await self._transport.close()

    async def _request(self, method: str, params: JsonObject) -> JsonObject:
        if self._initialize_result is None:
            raise AppServerClosedError("Codex app-server client has not been initialized")
        return await self._transport.request(method, params)


def _thread_params(
    cwd: str | Path | None,
    model: str | None,
    model_provider: str | None,
    approval_policy: ApprovalPolicy | None,
    sandbox: SandboxMode | None,
    config: Mapping[str, JsonValue] | None,
    developer_instructions: str | None,
    dynamic_tools: Sequence[JsonObject] | None,
    environments: Sequence[JsonObject] | None,
    runtime_workspace_roots: Sequence[str | Path] | None,
) -> JsonObject:
    values: dict[str, JsonValue | Path] = {
        "cwd": cwd,
        "model": model,
        "modelProvider": model_provider,
        "approvalPolicy": approval_policy,
        "sandbox": sandbox,
        "config": dict(config) if config is not None else None,
        "developerInstructions": developer_instructions,
        "dynamicTools": list(dynamic_tools) if dynamic_tools is not None else None,
        "environments": list(environments) if environments is not None else None,
        "runtimeWorkspaceRoots": (
            [str(root) for root in runtime_workspace_roots]
            if runtime_workspace_roots is not None
            else None
        ),
    }
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in values.items()
        if value is not None
    }


def _user_input(prompt: str, local_images: Sequence[str | Path]) -> list[JsonValue]:
    inputs: list[JsonValue] = []
    if prompt:
        inputs.append({"type": "text", "text": prompt})
    inputs.extend({"type": "localImage", "path": str(path)} for path in local_images)
    if not inputs:
        raise ValueError("Turn input must include text or an image")
    return inputs


def _parse_initialize_result(result: JsonObject) -> InitializeResult:
    return InitializeResult(
        user_agent=_string_field(result, "userAgent"),
        platform_family=_string_field(result, "platformFamily"),
        platform_os=_string_field(result, "platformOs"),
        codex_home=_string_field(result, "codexHome"),
    )


def _parse_thread(result: JsonObject) -> ThreadRef:
    approval = result.get("approvalPolicy")
    sandbox = result.get("sandbox")
    return ThreadRef(
        thread_id=_string_field(_object_field(result, "thread"), "id"),
        approval_policy=(
            cast(ApprovalPolicy, approval)
            if approval in {"untrusted", "on-request", "never"}
            else None
        ),
        sandbox_policy=dict(sandbox) if isinstance(sandbox, dict) else None,
    )


def _object_field(value: JsonObject, key: str) -> JsonObject:
    field = value.get(key)
    if not isinstance(field, dict):
        raise AppServerProtocolError(f"RPC response is missing object field {key!r}")
    return field


def _string_field(value: JsonObject, key: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field:
        raise AppServerProtocolError(f"RPC response is missing string field {key!r}")
    return field


def _nonempty(value: str, name: str) -> str:
    if not value:
        raise ValueError(f"{name.capitalize()} must not be empty")
    return value
