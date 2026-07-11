from study_discord_agent.agent_progress import progress_from_notification


def test_commentary_becomes_safe_status() -> None:
    progress = progress_from_notification(
        "item/completed",
        {
            "item": {
                "type": "agentMessage",
                "phase": "commentary",
                "text": "I found the session race and am fixing it now.\nMore detail.",
            }
        },
    )

    assert progress is not None
    assert progress.now == "Reviewing progress and continuing the work"


def test_reasoning_and_raw_commands_are_not_exposed() -> None:
    assert (
        progress_from_notification(
            "item/completed", {"item": {"type": "reasoning", "content": ["secret"]}}
        )
        is None
    )
    progress = progress_from_notification(
        "item/started",
        {"item": {"type": "commandExecution", "command": "echo $DISCORD_TOKEN"}},
    )

    assert progress is not None
    assert progress.now == "Running a repository command"
    assert "DISCORD_TOKEN" not in progress.now


def test_plan_uses_a_fixed_safe_next_step() -> None:
    progress = progress_from_notification(
        "item/completed",
        {"item": {"type": "plan", "text": "1. Inspect the gateway\n2. Run tests"}},
    )

    assert progress is not None
    assert progress.next_step == "Continue with the next planned step"


def test_structured_plan_update_preserves_step_statuses() -> None:
    progress = progress_from_notification(
        "turn/plan/updated",
        {
            "plan": [
                {"step": "Inspect the gateway", "status": "completed"},
                {"step": "  Add   native progress UI ", "status": "inProgress"},
                {"step": "Test it in Discord", "status": "pending"},
            ]
        },
    )

    assert progress is not None
    assert [(step.step, step.status) for step in progress.plan or ()] == [
        ("Inspect the gateway", "completed"),
        ("Add native progress UI", "inProgress"),
        ("Test it in Discord", "pending"),
    ]


def test_structured_plan_update_ignores_malformed_steps() -> None:
    progress = progress_from_notification(
        "turn/plan/updated",
        {
            "plan": [
                {"step": "", "status": "pending"},
                {"step": "invalid", "status": "done"},
                "not-a-step",
            ]
        },
    )

    assert progress is None
