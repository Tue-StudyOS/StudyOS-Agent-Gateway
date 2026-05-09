from study_discord_agent.github_events import notification_from_github_event


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


def test_unsupported_event_is_ignored() -> None:
    assert notification_from_github_event("push", {}) is None
