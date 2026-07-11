import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from study_discord_agent.agent_progress import AgentProgress
from study_discord_agent.codex_command import AgentUsage

ProgressSink = Callable[[AgentProgress], Awaitable[None]]


class SteerResult(Enum):
    STEERED = "steered"
    NO_ACTIVE_TURN = "no_active_turn"
    NOT_STEERABLE = "not_steerable"


class AgentTurnInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class AppServerTurnResult:
    message: str
    thread_id: str
    usage: AgentUsage


@dataclass
class ActiveTurn:
    channel_id: int
    thread_id: str
    turn_id: str
    done: asyncio.Future[AppServerTurnResult]
    progress: ProgressSink | None
    final_message: str | None = None
    fallback_message: str | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
