import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

from study_discord_agent.agent import AgentGateway
from study_discord_agent.config import Settings
from study_discord_agent.discord_mentions import DiscordMentionCoordinator
from study_discord_agent.discord_task_application import (
    DiscordTaskApplication,
    create_discord_task_application,
    default_discord_task_store_path,
)
from study_discord_agent.discord_task_component_controller import (
    DiscordTaskInteractionController,
)
from study_discord_agent.discord_task_controller import DiscordTaskController
from study_discord_agent.discord_task_messenger import DiscordTaskCardMessenger
from study_discord_agent.discord_task_service import DiscordTaskService
from study_discord_agent.discord_task_store import DiscordTaskStore


class FakeClient:
    def get_channel(self, channel_id: int) -> None:
        del channel_id
        return None

    async def fetch_channel(self, channel_id: int) -> object:
        raise AssertionError(f"unexpected channel fetch: {channel_id}")


class FakeService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reconcile_calls = 0
        self.close_calls = 0

    async def reconcile_startup(self) -> tuple[()]:
        self.reconcile_calls += 1
        self.events.append("reconcile")
        return ()

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append("service-close")


def _settings(tmp_path: Path, *, roots: str | None = None) -> Settings:
    return Settings(
        discord_token=SecretStr("test-token"),
        codex_home=str(tmp_path / "codex"),
        discord_attachment_dir=str(tmp_path / "attachments"),
        discord_artifact_allowed_roots=(str(tmp_path / "artifacts") if roots is None else roots),
    )


def test_default_task_store_path_uses_codex_gateway_directory(tmp_path: Path) -> None:
    assert default_discord_task_store_path(str(tmp_path)) == (
        tmp_path / "gateway" / "discord-tasks.json"
    )


@pytest.mark.asyncio
async def test_factory_constructs_one_shared_task_application(tmp_path: Path) -> None:
    application = create_discord_task_application(
        cast(Any, FakeClient()),
        _settings(tmp_path),
        cast(AgentGateway, object()),
    )

    assert isinstance(application.store, DiscordTaskStore)
    assert isinstance(application.presentation, DiscordTaskCardMessenger)
    assert isinstance(application.service, DiscordTaskService)
    assert isinstance(application.command_controller, DiscordTaskController)
    assert isinstance(
        application.component_controller,
        DiscordTaskInteractionController,
    )
    assert isinstance(application.mentions, DiscordMentionCoordinator)

    await application.close()


def test_factory_rejects_empty_artifact_policy(tmp_path: Path) -> None:
    with pytest.raises(
        RuntimeError,
        match="DISCORD_ARTIFACT_ALLOWED_ROOTS must contain at least one path",
    ):
        create_discord_task_application(
            cast(Any, FakeClient()),
            _settings(tmp_path, roots=""),
            cast(AgentGateway, object()),
        )


@pytest.mark.asyncio
async def test_reconciliation_runs_once_after_ready_and_before_close() -> None:
    events: list[str] = []
    service = FakeService(events)
    ready = asyncio.Event()

    async def wait_until_ready() -> None:
        events.append("waiting")
        await ready.wait()
        events.append("ready")

    application = DiscordTaskApplication(
        store=cast(Any, object()),
        delivery_cache=cast(Any, object()),
        presentation=cast(Any, object()),
        service=cast(Any, service),
        command_controller=cast(Any, object()),
        component_controller=cast(Any, object()),
        mentions=cast(Any, object()),
        command_group=cast(Any, object()),
        message_context_menu=cast(Any, object()),
    )

    application.start_reconciliation(wait_until_ready)
    application.start_reconciliation(wait_until_ready)
    await asyncio.sleep(0)
    assert service.reconcile_calls == 0

    ready.set()
    async with asyncio.timeout(0.5):
        while service.reconcile_calls == 0:
            await asyncio.sleep(0)
    await application.close()

    assert service.reconcile_calls == 1
    assert service.close_calls == 1
    assert events == ["waiting", "ready", "reconcile", "service-close"]
