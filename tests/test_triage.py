from study_discord_agent.triage import build_triage_prompt


def test_build_triage_prompt_includes_prs_and_issues() -> None:
    prompt = build_triage_prompt(
        "org/repo",
        [
            {
                "number": 1,
                "title": "Add wrapper",
                "html_url": "https://github.com/org/repo/pull/1",
                "updated_at": "2026-05-09T10:00:00Z",
                "user": {"login": "student"},
            }
        ],
        [
            {
                "number": 2,
                "title": "Document setup",
                "html_url": "https://github.com/org/repo/issues/2",
                "updated_at": "2026-05-09T11:00:00Z",
                "user": {"login": "maintainer"},
            }
        ],
    )

    assert "org/repo" in prompt
    assert "#1 Add wrapper" in prompt
    assert "#2 Document setup" in prompt
    assert "Never merge PRs" in prompt
