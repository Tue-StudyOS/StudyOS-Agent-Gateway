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


def test_unsupported_event_is_ignored() -> None:
    assert notification_from_github_event("push", {}) is None
