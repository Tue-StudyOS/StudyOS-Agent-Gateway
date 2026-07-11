from pathlib import Path

import pytest

from study_discord_agent.discord_reply_content import prepare_discord_reply


def test_short_reply_stays_inline(tmp_path: Path) -> None:
    prepared = prepare_discord_reply("Yep, that race is fixed.", (), tmp_path, 123)

    assert prepared.message == "Yep, that race is fixed."
    assert prepared.files == ()
    assert prepared.generated_file is None


def test_fenced_code_is_moved_to_markdown_attachment(tmp_path: Path) -> None:
    message = "Here's the fix:\n\n```python\nprint('hi')\n```"

    prepared = prepare_discord_reply(message, (), tmp_path, 123)

    assert "Full write-up's attached" in prepared.message
    assert len(prepared.files) == 1
    assert prepared.generated_file == prepared.files[0]
    assert prepared.files[0].name == "reply-123.md"
    assert prepared.files[0].read_text(encoding="utf-8") == message + "\n"


def test_long_reply_keeps_existing_artifacts_and_adds_markdown(tmp_path: Path) -> None:
    image = tmp_path / "diagram.png"
    image.write_bytes(b"png")
    message = "Useful summary. " + "detail " * 200

    prepared = prepare_discord_reply(message, (image,), tmp_path, 456)

    assert prepared.files == (image, tmp_path / "discord-replies/reply-456.md")
    assert len(prepared.message) < 500


def test_long_reply_fails_before_exceeding_discord_attachment_limit(tmp_path: Path) -> None:
    files = tuple(tmp_path / f"artifact-{index}.txt" for index in range(10))

    with pytest.raises(RuntimeError, match="already has 10 files"):
        prepare_discord_reply("detail " * 200, files, tmp_path, 789)

    assert not (tmp_path / "discord-replies/reply-789.md").exists()
