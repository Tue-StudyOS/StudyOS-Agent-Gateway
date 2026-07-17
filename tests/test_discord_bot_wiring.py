import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from pydantic import SecretStr

import study_discord_agent.discord_bot as discord_bot_module
from study_discord_agent.config import Settings
from study_discord_agent.discord_bot import StudyBot
from study_discord_agent.discord_task_components import DiscordTaskActionItem


def _settings(tmp_path: Path, *, guild_id: int | None = None) -> Settings:
    return Settings(
        discord_token=SecretStr("test-token"),
        discord_guild_id=guild_id,
        codex_home=str(tmp_path / "codex"),
        discord_attachment_dir=str(tmp_path / "attachments"),
        discord_artifact_allowed_roots=str(tmp_path / "artifacts"),
    )


def _bot(tmp_path: Path, *, guild_id: int | None = None) -> StudyBot:
    return StudyBot(
        _settings(tmp_path, guild_id=guild_id),
        cast(Any, object()),
        asyncio.Queue(),
        cast(Any, SimpleNamespace(pending_publication_ids=lambda: ())),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [None, 1234])
async def test_commands_dynamic_item_and_sync_scope_are_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guild_id: int | None,
) -> None:
    dynamic_items: list[type[object]] = []

    def add_dynamic_items(_bot: StudyBot, *items: type[object]) -> None:
        dynamic_items.extend(items)

    monkeypatch.setattr(StudyBot, "add_dynamic_items", add_dynamic_items)
    bot = _bot(tmp_path, guild_id=guild_id)
    bot.loop = asyncio.get_running_loop()
    guild = discord.Object(id=guild_id) if guild_id is not None else None
    sync_calls: list[object | None] = []

    async def sync(*, guild: object | None = None) -> list[object]:
        sync_calls.append(guild)
        return []

    def forbidden_clear(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"clear_commands called: {args!r} {kwargs!r}")

    async def no_worker() -> None:
        return None

    monkeypatch.setattr(bot.tree, "sync", sync)
    monkeypatch.setattr(bot.tree, "clear_commands", forbidden_clear)
    monkeypatch.setattr(bot, "_notification_worker", no_worker)
    monkeypatch.setattr(bot, "_publication_reconciler", no_worker)
    monkeypatch.setattr(bot, "wait_until_ready", no_worker)
    reconciled = 0

    async def reconcile() -> tuple[()]:
        nonlocal reconciled
        reconciled += 1
        return ()

    monkeypatch.setattr(bot.discord_tasks.service, "reconcile_startup", reconcile)

    await bot.setup_hook()
    async with asyncio.timeout(0.5):
        while reconciled == 0:
            await asyncio.sleep(0)

    assert isinstance(
        bot.tree.get_command("study", guild=guild),
        app_commands.Group,
    )
    assert isinstance(
        bot.tree.get_command(
            "Ask StudyOS about this",
            guild=guild,
            type=discord.AppCommandType.message,
        ),
        app_commands.ContextMenu,
    )
    assert sync_calls == [guild]
    assert DiscordTaskActionItem in dynamic_items
    assert bot.discord_task_component_controller is bot.discord_tasks.component_controller
    assert reconciled == 1

    await bot.discord_tasks.close()


class FakeTaskApplication:
    def __init__(self, events: list[str], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error
        self.mentions = object()

    def register(self, client: object) -> None:
        del client
        self.events.append("register")

    async def close(self) -> None:
        self.events.append("task-close")
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_bot_closes_task_runtime_before_discord_even_on_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    cleanup_error = RuntimeError("task cleanup failed")
    application = FakeTaskApplication(events, cleanup_error)

    def create_application(*_args: object) -> FakeTaskApplication:
        return application

    monkeypatch.setattr(
        discord_bot_module,
        "create_discord_task_application",
        create_application,
    )

    async def close_discord(_bot: commands.Bot) -> None:
        events.append("discord-close")

    monkeypatch.setattr(commands.Bot, "close", close_discord)
    bot = _bot(tmp_path)

    with pytest.raises(RuntimeError, match="task cleanup failed"):
        await bot.close()

    assert events == ["register", "task-close", "discord-close"]
