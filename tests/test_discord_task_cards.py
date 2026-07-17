from dataclasses import replace
from typing import cast

import discord
import pytest

from study_discord_agent.agent_progress import AgentPlanStep, AgentProgress
from study_discord_agent.discord_task_cards import build_task_card
from study_discord_agent.discord_task_model import (
    DiscordTaskFailure,
    DiscordTaskFailureCategory,
    DiscordTaskRetryMode,
    DiscordTaskState,
)
from study_discord_agent.discord_task_service_errors import DiscordTaskControlState
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"
NO_CONTROLS = DiscordTaskControlState(False, False, False)


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def _buttons(
    view: discord.ui.LayoutView,
) -> tuple[discord.ui.Button[discord.ui.LayoutView], ...]:
    return tuple(
        cast(discord.ui.Button[discord.ui.LayoutView], item)
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button)
    )


def _button_labels(view: discord.ui.LayoutView) -> set[str]:
    return {button.label or "" for button in _buttons(view)}


@pytest.mark.parametrize(
    ("state", "controls", "expected", "missing"),
    [
        (DiscordTaskState.STARTING, NO_CONTROLS, {"Stop task"}, {"Retry", "Why it failed"}),
        (
            DiscordTaskState.RUNNING,
            DiscordTaskControlState(True, False, False),
            {"Stop task", "Add context"},
            {"Retry"},
        ),
        (DiscordTaskState.STOPPING, NO_CONTROLS, set[str](), {"Stop task", "Retry"}),
        (DiscordTaskState.DELIVERING, NO_CONTROLS, set[str](), {"Stop task", "Retry"}),
        (DiscordTaskState.STOPPED, NO_CONTROLS, set[str](), {"Stop task", "Retry"}),
    ],
)
def test_card_controls_follow_public_task_state(
    state: DiscordTaskState,
    controls: DiscordTaskControlState,
    expected: set[str],
    missing: set[str],
) -> None:
    record = stored_record(TASK_ID, state)

    view = build_task_card(record, None, controls)

    assert view.timeout is None
    assert expected <= _button_labels(view)
    assert not (missing & _button_labels(view))
    assert f"`{TASK_ID[:8]}`" in _text(view)


def test_completed_card_links_result_and_only_latest_resumable_task_continues() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.COMPLETED),
        result_message_id=987,
    )

    view = build_task_card(
        record,
        None,
        DiscordTaskControlState(False, False, True),
    )

    buttons = _buttons(view)
    assert _button_labels(view) == {"View result", "Continue"}
    result = next(button for button in buttons if button.label == "View result")
    assert result.url == "https://discord.com/channels/2/10/987"
    continuation = next(button for button in buttons if button.label == "Continue")
    assert continuation.custom_id == f"studyos:task:continue:{TASK_ID}"


def test_card_links_back_to_the_source_message() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        origin_channel_id=11,
        source_message_id=321,
    )

    view = build_task_card(record, None, NO_CONTROLS)

    source = next(button for button in _buttons(view) if button.label == "View request")
    assert source.url == "https://discord.com/channels/2/11/321"


@pytest.mark.parametrize(
    ("retry_mode", "resumable", "has_retry"),
    [
        (DiscordTaskRetryMode.NONE, False, False),
        (DiscordTaskRetryMode.CONTINUE_SESSION, False, False),
        (DiscordTaskRetryMode.CONTINUE_SESSION, True, True),
        (DiscordTaskRetryMode.RETRY_DELIVERY, False, True),
    ],
)
def test_failure_card_explains_safe_reason_and_only_offers_safe_retry(
    retry_mode: DiscordTaskRetryMode,
    resumable: bool,
    has_retry: bool,
) -> None:
    delivery_retry = retry_mode is DiscordTaskRetryMode.RETRY_DELIVERY
    failure = DiscordTaskFailure(
        (
            DiscordTaskFailureCategory.DISCORD_DELIVERY
            if delivery_retry
            else DiscordTaskFailureCategory.RUNTIME_DISCONNECTED
        ),
        "Codex disconnected safely. @everyone **do not ping**",
        retry_mode,
    )
    record = stored_record(
        TASK_ID,
        DiscordTaskState.DELIVERY_FAILED if delivery_retry else DiscordTaskState.FAILED,
        failure=failure,
    )

    view = build_task_card(
        record,
        None,
        DiscordTaskControlState(False, resumable, False),
    )

    assert "Why it failed" in _button_labels(view)
    assert ("Retry" in _button_labels(view)) is has_retry
    assert "@\u200beveryone" in _text(view)
    assert "\\*\\*do not ping\\*\\*" in _text(view)
    assert "Stop task" not in _button_labels(view)


def test_progress_is_bounded_escaped_and_does_not_overflow_card() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        source_label="@here **dangerous source**",
    )
    progress = AgentProgress(
        now="@everyone " + "x" * 5_000,
        plan=tuple(AgentPlanStep(f"step {index} **unsafe**", "pending") for index in range(20)),
    )

    view = build_task_card(record, progress, NO_CONTROLS)
    rendered = _text(view)

    assert len(rendered) <= 3_900
    assert "@\u200beveryone" in rendered
    assert "@\u200bhere" in rendered
    assert "\\*\\*dangerous source\\*\\*" in rendered
    assert "step 5" in rendered
    assert "step 6" not in rendered


def test_every_state_renders_a_components_v2_card() -> None:
    for state in DiscordTaskState:
        if state is DiscordTaskState.DELIVERY_FAILED:
            record = stored_record(
                TASK_ID,
                state,
                failure=DiscordTaskFailure(
                    DiscordTaskFailureCategory.DISCORD_DELIVERY,
                    "Discord delivery failed.",
                    DiscordTaskRetryMode.NONE,
                ),
            )
        else:
            record = stored_record(TASK_ID, state)
        view = build_task_card(record, None, NO_CONTROLS)
        assert isinstance(view, discord.ui.LayoutView)
        assert _text(view)
