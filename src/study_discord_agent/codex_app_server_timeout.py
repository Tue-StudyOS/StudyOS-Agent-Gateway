import asyncio
import logging

from study_discord_agent.codex_app_server_turn import (
    AgentTurnInterrupted,
    AppServerTurnResult,
)

logger = logging.getLogger(__name__)


async def wait_for_turn_shutdown(
    done: asyncio.Future[AppServerTurnResult],
    grace_seconds: float,
) -> None:
    """Give an interrupted turn a bounded window to reach a terminal state."""
    try:
        await asyncio.wait_for(asyncio.shield(done), timeout=grace_seconds)
    except (AgentTurnInterrupted, TimeoutError):
        return
    except Exception as error:
        logger.warning("Codex turn failed while stopping after timeout: %s", error)
