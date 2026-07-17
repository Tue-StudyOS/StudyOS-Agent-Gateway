from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from discord import app_commands

from study_discord_agent.discord_task_command_views import DiscordTaskForgetView
from study_discord_agent.discord_task_commands import (
    DISCORD_MESSAGE_LIMIT,
    StudyCommandGroup,
    create_message_context_menu,
)
from study_discord_agent.discord_task_controller import (
    DiscordTaskCommandError,
    DiscordTaskController,
)
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import DiscordTaskSourceKind, DiscordTaskState
from tests.discord_task_command_fakes import (
    TASK_ID,
    FakePermissions,
)
from tests.discord_task_command_fakes import (
    FakeChannel as _Channel,
)
from tests.discord_task_command_fakes import (
    FakeInteraction as _Interaction,
)
from tests.discord_task_command_fakes import (
    FakeStore as _Store,
)
from tests.discord_task_command_fakes import (
    create_controller as _controller,
)
from tests.test_discord_task_service_fixtures import stored_record


@pytest.mark.asyncio
async def test_slash_start_uses_channel_task_path_without_source_message(
    tmp_path: Path,
) -> None:
    controller, service, _ = _controller(tmp_path)
    interaction = _Interaction(_Channel())

    await controller.start_slash(cast(Any, interaction), "Inspect the repository", False)

    request = service.starts[0]
    assert request.source_kind is DiscordTaskSourceKind.SLASH
    assert request.execution_channel_id == 10
    assert request.source_message_id is None
    assert request.prompt == "Inspect the repository"


@pytest.mark.asyncio
async def test_dedicated_thread_is_neutral_and_never_falls_back(
    tmp_path: Path,
) -> None:
    controller, service, _ = _controller(tmp_path)
    channel = _Channel()
    interaction = _Interaction(channel)

    await controller.start_slash(cast(Any, interaction), "private prompt text", True)

    assert channel.created_names == ["studyos-task"]
    assert "private" not in channel.created_names[0]
    assert service.starts[0].origin_channel_id == 10
    assert service.starts[0].execution_channel_id == 44

    unsupported = _Interaction(_Channel(supports_threads=False), interaction_id=901)
    with pytest.raises(DiscordTaskCommandError, match="text channel"):
        await controller.start_slash(cast(Any, unsupported), "task", True)
    assert len(service.starts) == 1


@pytest.mark.asyncio
async def test_dedicated_thread_requires_actor_and_bot_thread_permissions(
    tmp_path: Path,
) -> None:
    controller, service, _ = _controller(tmp_path)
    actor_denied = _Channel(
        actor_permissions=FakePermissions(create_public_threads=False)
    )

    with pytest.raises(DiscordTaskCommandError, match="You cannot"):
        await controller.start_slash(
            cast(Any, _Interaction(actor_denied)), "task", True
        )

    bot_denied = _Channel(
        bot_permissions=FakePermissions(send_messages_in_threads=False)
    )
    with pytest.raises(DiscordTaskCommandError, match="StudyOS cannot"):
        await controller.start_slash(
            cast(Any, _Interaction(bot_denied, interaction_id=901)), "task", True
        )

    assert not actor_denied.created_names
    assert not bot_denied.created_names
    assert not service.starts


@pytest.mark.asyncio
async def test_failed_task_start_removes_new_dedicated_thread(tmp_path: Path) -> None:
    controller, service, _ = _controller(tmp_path)
    service.start_error = RuntimeError("store unavailable")
    channel = _Channel()

    with pytest.raises(RuntimeError, match="store unavailable"):
        await controller.start_slash(
            cast(Any, _Interaction(channel)), "task", True
        )

    assert channel.thread.deleted


@pytest.mark.asyncio
async def test_context_action_stages_selected_message_and_uses_its_source(
    tmp_path: Path,
) -> None:
    staged = StagedDiscordAttachments(paths=(), directory=None)
    staged_messages: list[int] = []

    async def stage(message: Any, _root: Path, *, trigger_event_id: int):
        staged_messages.append(message.id)
        assert trigger_event_id == 900
        return staged

    controller, service, _ = _controller(tmp_path)
    controller = DiscordTaskController(
        store=_Store(),
        service=cast(Any, service),
        attachment_root=tmp_path,
        stage_attachments=cast(Any, stage),
    )
    interaction = _Interaction(_Channel())
    message = SimpleNamespace(
        id=77,
        guild=interaction.guild,
        channel=interaction.channel,
        attachments=[],
    )

    await controller.start_message_context(
        cast(Any, interaction),
        cast(Any, message),
        "Explain this message",
    )

    request = service.starts[0]
    assert staged_messages == [77]
    assert request.source_kind is DiscordTaskSourceKind.CONTEXT_ACTION
    assert request.source_message_id == 77
    assert request.attachments is staged


def test_native_commands_and_context_menu_are_declared(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    group = StudyCommandGroup(controller)
    context_menu = create_message_context_menu(controller)

    assert {command.name for command in group.walk_commands()} == {
        "ask",
        "tasks",
        "status",
    }
    assert context_menu.name == "Ask StudyOS about this"
    assert isinstance(context_menu, app_commands.ContextMenu)
    assert context_menu.guild_only


@pytest.mark.asyncio
async def test_missing_slash_prompt_opens_modal_before_any_defer(tmp_path: Path) -> None:
    controller, service, _ = _controller(tmp_path)
    group = StudyCommandGroup(controller)
    interaction = _Interaction(_Channel())
    ask = group.get_command("ask")
    assert isinstance(ask, app_commands.Command)

    await ask.callback(cast(Any, group), cast(Any, interaction), None, False)

    assert interaction.response.events == ["modal"]
    assert not service.starts


@pytest.mark.asyncio
async def test_slash_prompt_defers_then_starts_and_responds_ephemerally(
    tmp_path: Path,
) -> None:
    controller, service, _ = _controller(tmp_path)
    group = StudyCommandGroup(controller)
    interaction = _Interaction(_Channel())
    ask = group.get_command("ask")
    assert isinstance(ask, app_commands.Command)

    await ask.callback(
        cast(Any, group),
        cast(Any, interaction),
        "Run focused tests",
        False,
    )

    assert interaction.response.events == ["defer"]
    assert service.starts[0].prompt == "Run focused tests"
    assert interaction.followup.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_context_menu_opens_instruction_modal_as_first_response(
    tmp_path: Path,
) -> None:
    controller, service, _ = _controller(tmp_path)
    menu = create_message_context_menu(controller)
    interaction = _Interaction(_Channel())
    message = SimpleNamespace(id=77)

    await menu.callback(cast(Any, interaction), cast(Any, message))

    assert interaction.response.events == ["modal"]
    assert not service.starts


@pytest.mark.asyncio
async def test_status_is_ephemeral_and_owner_can_confirm_forget(
    tmp_path: Path,
) -> None:
    record = stored_record(TASK_ID, DiscordTaskState.COMPLETED)
    controller, service, _ = _controller(tmp_path, (record,))
    group = StudyCommandGroup(controller)
    interaction = _Interaction(_Channel())
    status = group.get_command("status")
    assert isinstance(status, app_commands.Command)

    await status.callback(cast(Any, group), cast(Any, interaction), TASK_ID)

    result = interaction.followup.messages[0]
    assert interaction.response.events == ["defer"]
    assert result["ephemeral"] is True
    view = result["view"]
    assert isinstance(view, DiscordTaskForgetView)

    request = _Interaction(_Channel(), interaction_id=901)
    await view.children[0].callback(cast(Any, request))
    confirm = request.response.messages[0]["view"]
    assert isinstance(confirm, discord.ui.View)
    approval = _Interaction(_Channel(), interaction_id=902)
    await confirm.children[0].callback(cast(Any, approval))
    assert service.forgotten == [TASK_ID]


@pytest.mark.asyncio
async def test_manual_status_id_still_checks_current_channel_visibility(
    tmp_path: Path,
) -> None:
    record = stored_record(TASK_ID, DiscordTaskState.COMPLETED)
    controller, _, _ = _controller(tmp_path, (record,))

    with pytest.raises(PermissionError, match="channel"):
        await controller.status(
            cast(Any, _Interaction(_Channel(channel_id=99))),
            TASK_ID,
        )


@pytest.mark.asyncio
async def test_autocomplete_is_authorized_filtered_and_bounded(tmp_path: Path) -> None:
    records = tuple(
        replace(
            stored_record(f"{index + 1:032x}", DiscordTaskState.COMPLETED),
            card_message_id=100 + index,
            source_label=f"Task {index}",
        )
        for index in range(15)
    )
    controller, _, _ = _controller(tmp_path, records)

    choices = await controller.autocomplete(
        cast(Any, _Interaction(_Channel())),
        "task",
    )

    assert len(choices) == 10
    assert all(isinstance(choice, app_commands.Choice) for choice in choices)


@pytest.mark.asyncio
async def test_task_list_response_stays_within_discord_message_limit(
    tmp_path: Path,
) -> None:
    records = tuple(
        replace(
            stored_record(f"{index + 1:032x}", DiscordTaskState.COMPLETED),
            card_message_id=100 + index,
            source_label="*" * 200,
        )
        for index in range(10)
    )
    controller, _, _ = _controller(tmp_path, records)
    group = StudyCommandGroup(controller)
    interaction = _Interaction(_Channel())
    tasks = group.get_command("tasks")
    assert isinstance(tasks, app_commands.Command)

    await tasks.callback(cast(Any, group), cast(Any, interaction), None, None)

    content = interaction.followup.messages[0]["content"]
    assert isinstance(content, str)
    assert len(content) <= DISCORD_MESSAGE_LIMIT
    assert "more matching task" in content


@pytest.mark.asyncio
async def test_task_filters_run_before_live_channel_resolution(tmp_path: Path) -> None:
    mine = stored_record(TASK_ID, DiscordTaskState.COMPLETED)
    foreign = replace(
        stored_record("00000000000000000000000000000002", DiscordTaskState.COMPLETED),
        owner_id=2,
        execution_channel_id=99,
        origin_channel_id=99,
    )
    controller, _, _ = _controller(tmp_path, (mine, foreign))
    interaction = _Interaction(_Channel())

    records = await controller.visible_tasks(
        cast(Any, interaction), scope="mine", state="all"
    )

    assert records == (mine,)
    assert interaction.guild.fetch_calls == []
