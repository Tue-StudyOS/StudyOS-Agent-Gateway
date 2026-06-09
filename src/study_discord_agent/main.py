import asyncio
import logging

import uvicorn

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import load_settings
from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.git_identity import ensure_git_identity_from_gh
from study_discord_agent.github_client import GitHubClient
from study_discord_agent.github_events import DiscordNotification
from study_discord_agent.memory import ensure_global_agents, ensure_studyos_memory
from study_discord_agent.triage import run_github_triage_loop
from study_discord_agent.web import create_app


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    ensure_global_agents(settings.codex_home)
    ensure_studyos_memory(settings.codex_home)
    ensure_git_identity_from_gh()

    queue: asyncio.Queue[DiscordNotification] = asyncio.Queue()
    github = GitHubClient(settings.github_token_value)
    agent = AgentGateway(
        webhook_url=settings.agent_webhook_url,
        command=settings.agent_command,
        workdir=settings.agent_workdir,
        timeout_seconds=settings.agent_timeout_seconds,
        channel_sessions_enabled=settings.agent_channel_sessions_enabled,
        session_store_path=settings.agent_session_store_path,
        codex_home=settings.codex_home,
    )
    bot = StudyBot(settings, github, agent, queue)
    app = create_app(settings, queue)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level,
        ),
    )

    async with bot:
        tasks = [
            bot.start(settings.discord_token_value),
            server.serve(),
        ]
        if settings.github_poll_enabled:
            tasks.append(
                run_github_triage_loop(
                    settings,
                    github,
                    agent,
                    bot.publish_agent_message,
                ),
            )
        await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
