import re
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.discord_task_components import (
    TASK_COMPONENT_TEMPLATE,
    DiscordTaskActionItem,
    DiscordTaskComponentAction,
)

TASK_HEX = "00000000000000000000000000000001"


class _Response:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool, discord.AllowedMentions | None]] = []

    async def send_message(
        self,
        content: str,
        *,
        ephemeral: bool = False,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        self.messages.append((content, ephemeral, allowed_mentions))


class _Controller:
    def __init__(self) -> None:
        self.calls: list[tuple[DiscordTaskComponentAction, str, object]] = []

    async def handle_task_action(
        self,
        action: DiscordTaskComponentAction,
        task_id: str,
        interaction: discord.Interaction,
    ) -> None:
        self.calls.append((action, task_id, interaction))


def _interaction(controller: object | None = None) -> SimpleNamespace:
    client = SimpleNamespace()
    if controller is not None:
        client.discord_task_component_controller = controller
    return SimpleNamespace(client=client, response=_Response())


@pytest.mark.parametrize(
    "custom_id",
    [
        f"studyos:task:stop:{TASK_HEX}",
        f"studyos:task:add_context:{TASK_HEX}",
        f"studyos:task:retry:{TASK_HEX}",
        f"studyos:task:why:{TASK_HEX}",
        f"studyos:task:continue:{TASK_HEX}",
    ],
)
def test_dynamic_component_template_is_exactly_anchored(custom_id: str) -> None:
    assert re.fullmatch(TASK_COMPONENT_TEMPLATE, custom_id)
    assert not re.fullmatch(TASK_COMPONENT_TEMPLATE, f"prefix:{custom_id}")
    assert not re.fullmatch(TASK_COMPONENT_TEMPLATE, f"{custom_id}:suffix")
    assert not re.fullmatch(TASK_COMPONENT_TEMPLATE, custom_id.upper())


@pytest.mark.asyncio
async def test_dynamic_item_reconstructs_after_restart_and_routes_via_client() -> None:
    controller = _Controller()
    interaction = _interaction(controller)
    custom_id = f"studyos:task:retry:{TASK_HEX}"
    match = re.fullmatch(TASK_COMPONENT_TEMPLATE, custom_id)
    assert match is not None
    button = discord.ui.Button[discord.ui.LayoutView](
        label="Retry",
        custom_id=custom_id,
    )

    item = await DiscordTaskActionItem.from_custom_id(
        cast(Any, interaction),
        button,
        match,
    )
    await item.callback(cast(Any, interaction))

    assert item.action is DiscordTaskComponentAction.RETRY
    assert item.task_id == TASK_HEX
    assert controller.calls == [
        (DiscordTaskComponentAction.RETRY, TASK_HEX, interaction)
    ]


@pytest.mark.asyncio
async def test_dynamic_item_fails_ephemerally_when_controller_is_unavailable() -> None:
    interaction = _interaction()
    custom_id = f"studyos:task:stop:{TASK_HEX}"
    match = re.fullmatch(TASK_COMPONENT_TEMPLATE, custom_id)
    assert match is not None
    button = discord.ui.Button[discord.ui.LayoutView](label="Stop", custom_id=custom_id)
    item = await DiscordTaskActionItem.from_custom_id(
        cast(Any, interaction), button, match
    )

    await item.callback(cast(Any, interaction))

    assert interaction.response.messages
    content, ephemeral, allowed_mentions = interaction.response.messages[0]
    assert "temporarily unavailable" in content
    assert ephemeral
    assert allowed_mentions is not None
