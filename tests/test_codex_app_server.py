import asyncio
import sys
from pathlib import Path

import pytest

from study_discord_agent.codex_app_server import CodexAppServerClient
from study_discord_agent.codex_app_server_protocol import (
    AppServerNotification,
    AppServerProcessError,
    AppServerProtocolError,
    AppServerRpcError,
)

FAKE_SERVER = r"""
import json
import sys

initialized = False

def send(message):
    print(json.dumps(message), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if method == "initialize":
        if params.get("capabilities") != {"experimentalApi": True}:
            send({"id": request_id, "error": {
                "code": -32600, "message": "Experimental API capability required"
            }})
            continue
        send({"id": request_id, "result": {
            "userAgent": "codex-test/1",
            "platformFamily": "unix",
            "platformOs": "linux",
            "codexHome": "/tmp/codex",
        }})
    elif method == "initialized":
        initialized = True
    elif not initialized:
        send({"id": request_id, "error": {"code": -1, "message": "Not initialized"}})
    elif method == "thread/resume" and params["threadId"] == "missing":
        send({"id": request_id, "error": {
            "code": 404,
            "message": "Thread not found",
            "data": {"threadId": "missing"},
        }})
    elif method == "thread/resume" and params["threadId"] == "large":
        send({"id": request_id, "result": {
            "thread": {"id": "large", "history": "x" * 100_000},
        }})
    elif method in ("thread/start", "thread/resume"):
        thread_id = params.get("threadId", "thread-1")
        send({"method": "test/observed", "params": {"method": method, "params": params}})
        send({"id": request_id, "result": {"thread": {"id": thread_id}}})
    elif method == "turn/start":
        send({"method": "turn/started", "params": {
            "threadId": params["threadId"],
            "turn": {"id": "turn-1", "status": "inProgress", "items": []},
        }})
        send({"id": request_id, "result": {
            "turn": {"id": "turn-1", "status": "inProgress", "items": []},
        }})
    elif method == "turn/steer":
        send({"method": "test/observed", "params": {"method": method, "params": params}})
        send({"id": request_id, "result": {"turnId": params["expectedTurnId"]}})
    elif method == "turn/interrupt":
        send({"method": "turn/completed", "params": {
            "threadId": params["threadId"],
            "turn": {"id": params["turnId"], "status": "interrupted", "items": []},
        }})
        send({"id": request_id, "result": {}})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": "Unknown method"}})
"""


def _command(script: str = FAKE_SERVER) -> tuple[str, ...]:
    return (sys.executable, "-u", "-c", script)


@pytest.mark.asyncio
async def test_thread_turn_lifecycle_and_notifications(tmp_path: Path) -> None:
    notifications: list[AppServerNotification] = []
    interrupted = asyncio.Event()

    async def handle(notification: AppServerNotification) -> None:
        notifications.append(notification)
        if notification.method == "turn/completed":
            interrupted.set()

    client = CodexAppServerClient(_command())
    unsubscribe = client.subscribe(handle)
    async with client:
        initialize = await client.start()
        assert initialize.user_agent == "codex-test/1"
        assert initialize.platform_os == "linux"

        thread = await client.start_thread(
            cwd=tmp_path,
            model="gpt-test",
            approval_policy="never",
            sandbox="danger-full-access",
            config={"web_search": "live"},
            developer_instructions="Be concise.",
        )
        resumed = await client.resume_thread(thread.thread_id, cwd=tmp_path)
        turn = await client.start_turn(
            thread.thread_id,
            "Inspect this image",
            local_images=(tmp_path / "input.png",),
        )
        steered = await client.steer_turn(thread.thread_id, turn.turn_id, "Focus on tests")
        await client.interrupt_turn(thread.thread_id, turn.turn_id)
        await asyncio.wait_for(interrupted.wait(), timeout=1)

    unsubscribe()
    assert thread.thread_id == "thread-1"
    assert resumed.thread_id == thread.thread_id
    assert turn.turn_id == "turn-1"
    assert steered == turn
    assert [notification.method for notification in notifications] == [
        "test/observed",
        "test/observed",
        "turn/started",
        "test/observed",
        "turn/completed",
    ]
    thread_params = notifications[0].params["params"]
    assert isinstance(thread_params, dict)
    assert thread_params == {
        "cwd": str(tmp_path),
        "model": "gpt-test",
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
        "config": {"web_search": "live"},
        "developerInstructions": "Be concise.",
    }
    steer_params = notifications[3].params["params"]
    assert isinstance(steer_params, dict)
    assert steer_params["expectedTurnId"] == "turn-1"


@pytest.mark.asyncio
async def test_rpc_error_preserves_code_message_and_data() -> None:
    async with CodexAppServerClient(_command()) as client:
        with pytest.raises(AppServerRpcError) as exc_info:
            await client.resume_thread("missing")

    assert exc_info.value.code == 404
    assert exc_info.value.message == "Thread not found"
    assert exc_info.value.data == {"threadId": "missing"}


@pytest.mark.asyncio
async def test_large_thread_resume_response_exceeds_default_asyncio_limit() -> None:
    async with CodexAppServerClient(_command()) as client:
        thread = await client.resume_thread("large")

    assert thread.thread_id == "large"


@pytest.mark.asyncio
async def test_invalid_json_fails_initialization_and_closes_process() -> None:
    script = "import sys; sys.stdin.readline(); print('invalid json', flush=True)"
    client = CodexAppServerClient(_command(script), shutdown_timeout=0.1)

    with pytest.raises(AppServerProtocolError, match="invalid JSON"):
        await client.start()

    await client.close()


@pytest.mark.asyncio
async def test_transport_exit_notification_preserves_process_error() -> None:
    script = r"""
import json
import sys

request = json.loads(sys.stdin.readline())
print(json.dumps({"id": request["id"], "result": {
    "userAgent": "codex-test/1", "platformFamily": "unix",
    "platformOs": "linux", "codexHome": "/tmp/codex",
}}), flush=True)
sys.stdin.readline()
"""
    exited = asyncio.Event()
    notifications: list[AppServerNotification] = []

    async def handle(notification: AppServerNotification) -> None:
        notifications.append(notification)
        if notification.method == "app-server/exited":
            exited.set()

    client = CodexAppServerClient(_command(script))
    client.subscribe(handle)
    await client.start()
    await asyncio.wait_for(exited.wait(), timeout=1)
    await client.close()

    assert isinstance(notifications[-1].error, AppServerProcessError)


@pytest.mark.asyncio
async def test_notification_handler_failure_does_not_block_other_handlers() -> None:
    received = asyncio.Event()

    async def failing(_notification: AppServerNotification) -> None:
        raise RuntimeError("handler failed")

    async def healthy(_notification: AppServerNotification) -> None:
        received.set()

    client = CodexAppServerClient(_command())
    client.subscribe(failing)
    client.subscribe(healthy)
    async with client:
        await client.start_thread()
        await asyncio.wait_for(received.wait(), timeout=1)


@pytest.mark.asyncio
async def test_close_terminates_process_that_ignores_stdin_eof() -> None:
    script = r"""
import json
import sys
import time

message = json.loads(sys.stdin.readline())
print(json.dumps({"id": message["id"], "result": {
    "userAgent": "codex-test/1", "platformFamily": "unix",
    "platformOs": "linux", "codexHome": "/tmp/codex",
}}), flush=True)
sys.stdin.readline()
sys.stdin.read()
while True:
    time.sleep(1)
"""
    client = CodexAppServerClient(_command(script), shutdown_timeout=0.05)
    await client.start()

    await asyncio.wait_for(client.close(), timeout=1)
