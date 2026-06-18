from study_discord_agent.github_events import AGENT_COMMENT_MARKER, notification_from_github_event


def test_pull_request_notification() -> None:
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 7,
            "title": "Add course API wrapper",
            "html_url": "https://github.com/org/repo/pull/7",
            "merged": False,
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "student"},
    }

    notification = notification_from_github_event("pull_request", payload)

    assert notification is not None
    assert notification.title == "PR #7 opened: Add course API wrapper"
    assert notification.url == "https://github.com/org/repo/pull/7"
    assert notification.followup_message is not None
    assert "Humans own the final merge" in notification.followup_message
    assert notification.agent_prompt is not None
    assert AGENT_COMMENT_MARKER in notification.agent_prompt


def test_pull_request_synchronize_does_not_auto_run_agent() -> None:
    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 7,
            "title": "Add course API wrapper",
            "html_url": "https://github.com/org/repo/pull/7",
            "merged": False,
        },
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "student"},
    }

    notification = notification_from_github_event("pull_request", payload)

    assert notification is not None
    assert notification.title == "PR #7 synchronize: Add course API wrapper"
    assert notification.followup_message is None
    assert notification.agent_prompt is None


def test_issue_comment_notification_prompts_refinement() -> None:
    payload = {
        "action": "created",
        "issue": {
            "number": 12,
            "title": "Clarify wrapper setup",
            "html_url": "https://github.com/org/repo/issues/12",
        },
        "comment": {"body": "I am not sure which auth flow to use."},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "student"},
    }

    notification = notification_from_github_event("issue_comment", payload)

    assert notification is not None
    assert notification.title == "Issue #12 comment: Clarify wrapper setup"
    assert notification.agent_prompt is not None
    assert "Ask clarifying questions" in notification.agent_prompt
    assert AGENT_COMMENT_MARKER in notification.agent_prompt


def test_issue_comment_with_agent_marker_is_ignored() -> None:
    payload = {
        "action": "created",
        "issue": {
            "number": 12,
            "title": "Clarify wrapper setup",
            "html_url": "https://github.com/org/repo/issues/12",
        },
        "comment": {"body": f"Follow-up from the agent.\n\n{AGENT_COMMENT_MARKER}"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "student"},
    }

    assert notification_from_github_event("issue_comment", payload) is None


def test_issue_comment_from_bot_sender_is_ignored() -> None:
    payload = {
        "action": "created",
        "issue": {
            "number": 12,
            "title": "Clarify wrapper setup",
            "html_url": "https://github.com/org/repo/issues/12",
        },
        "comment": {"body": "Automated follow-up."},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "studyos-agent[bot]", "type": "Bot"},
    }

    assert notification_from_github_event("issue_comment", payload) is None


def test_unsupported_event_is_ignored() -> None:
    assert notification_from_github_event("push", {}) is None
