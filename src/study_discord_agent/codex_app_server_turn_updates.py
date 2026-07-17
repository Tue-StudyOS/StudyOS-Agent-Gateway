import asyncio
from collections.abc import Mapping
from typing import cast

from study_discord_agent.agent_progress import progress_from_notification
from study_discord_agent.codex_app_server_events import (
    agent_message,
    notification_turn_id,
    turn_error_message,
    usage_from_notification,
)
from study_discord_agent.codex_app_server_protocol import AppServerNotification
from study_discord_agent.codex_app_server_turn import (
    ActiveTurn,
    AgentTurnInterrupted,
    AppServerTurnResult,
)


async def state_for_notification(
    lock: asyncio.Lock,
    active_turns: Mapping[int, ActiveTurn],
    params: Mapping[str, object],
) -> ActiveTurn | None:
    thread_id = params.get("threadId")
    turn_id = notification_turn_id(params)
    async with lock:
        return next(
            (
                state
                for state in active_turns.values()
                if state.thread_id == thread_id and state.turn_id == turn_id
            ),
            None,
        )


async def process_notification(
    notification: AppServerNotification,
    state: ActiveTurn,
) -> None:
    params = cast(dict[str, object], dict(notification.params))
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
        complete_turn(state, params)


def complete_turn(state: ActiveTurn, params: Mapping[str, object]) -> None:
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
