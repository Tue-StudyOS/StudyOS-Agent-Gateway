import asyncio
import logging

import uvicorn

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import load_settings
from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.github_client import GitHubClient
from study_discord_agent.github_events import DiscordNotification
from study_discord_agent.triage import run_github_triage_loop
from study_discord_agent.web import create_app


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())

    queue: asyncio.Queue[DiscordNotification] = asyncio.Queue()
    github = GitHubClient(settings.github_token_value, settings.github_write_enabled)
    agent = AgentGateway(
        settings.agent_webhook_url,
        settings.agent_command,
        settings.agent_workdir,
        settings.agent_timeout_seconds,
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
