import asyncio
import re
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from study_discord_agent import discord_task_inputs
from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_task_inputs import (
    MAX_DISCORD_INPUT_ATTACHMENT_BYTES,
    StagedDiscordAttachments,
    stage_message_attachments,
)


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        payload: bytes,
        *,
        declared_size: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.filename = filename
        self.size = len(payload) if declared_size is None else declared_size
        self.payload = payload
        self.error = error
        self.save_calls = 0

    async def save(self, destination: Any) -> int:
        self.save_calls += 1
        if self.error is not None:
            raise self.error
        if hasattr(destination, "write"):
            return destination.write(self.payload)
        return Path(destination).write_bytes(self.payload)


class FakeMessage:
    def __init__(self, message_id: int, attachments: list[FakeAttachment]) -> None:
        self.id = message_id
        self.attachments = attachments


@pytest.mark.asyncio
async def test_no_attachments_returns_unowned_empty_stage(tmp_path: Path) -> None:
    root = tmp_path / "attachments"

    staged = await stage_message_attachments(
        cast(Any, FakeMessage(99, [])),
        root,
        trigger_event_id=42,
    )

    assert staged == StagedDiscordAttachments(paths=(), directory=None)
    staged.cleanup()
    assert not root.exists()


@pytest.mark.asyncio
async def test_eleventh_attachment_is_rejected_before_directory_or_download(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    attachments = [FakeAttachment(f"{index}.txt", b"ok") for index in range(11)]

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="at most 10"):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, attachments)),
            root,
            trigger_event_id=42,
        )

    assert not root.exists()
    assert all(attachment.save_calls == 0 for attachment in attachments)


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_before_directory_or_any_download(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    first = FakeAttachment("first.txt", b"ok")
    oversize = FakeAttachment(
        "large.bin",
        b"small",
        declared_size=MAX_DISCORD_INPUT_ATTACHMENT_BYTES + 1,
    )

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="8,000,000"):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [first, oversize])),
            root,
            trigger_event_id=42,
        )

    assert not root.exists()
    assert first.save_calls == 0
    assert oversize.save_calls == 0


@pytest.mark.asyncio
async def test_actual_oversize_removes_private_stage(tmp_path: Path) -> None:
    root = tmp_path / "attachments"
    attachment = FakeAttachment(
        "large.bin",
        b"x" * (MAX_DISCORD_INPUT_ATTACHMENT_BYTES + 1),
        declared_size=1,
    )

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="8,000,000"):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [attachment])),
            root,
            trigger_event_id=42,
        )

    assert attachment.save_calls == 1
    assert root.exists()
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_partial_save_failure_removes_every_staged_file(tmp_path: Path) -> None:
    root = tmp_path / "attachments"
    first = FakeAttachment("first.txt", b"saved")
    failure = FakeAttachment(
        "private-name.txt",
        b"",
        error=OSError("/private/secret-path"),
    )

    with pytest.raises(
        AgentWorkspaceOrAttachmentError,
        match="Discord attachments could not be staged safely",
    ) as raised:
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [first, failure])),
            root,
            trigger_event_id=42,
        )

    assert "/private/secret-path" not in str(raised.value)
    assert first.save_calls == failure.save_calls == 1
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_cancellation_preserves_cancellation_and_removes_private_stage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    first = FakeAttachment("first.txt", b"saved")
    cancelled = FakeAttachment("second.txt", b"", error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [first, cancelled])),
            root,
            trigger_event_id=42,
        )

    assert first.save_calls == cancelled.save_calls == 1
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_stage_uses_explicit_trigger_private_modes_and_bounded_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir()
    unrelated = root / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    predictable = root / "42"
    predictable.symlink_to(unrelated, target_is_directory=True)
    attachment = FakeAttachment(f"../{'x' * 400} weird?.txt", b"payload")

    staged = await stage_message_attachments(
        cast(Any, FakeMessage(777, [attachment])),
        root,
        trigger_event_id=42,
    )

    assert staged.directory is not None
    assert staged.directory.parent == root
    assert staged.directory.name.startswith("42-")
    assert not staged.directory.name.startswith("777-")
    assert staged.directory != predictable
    assert stat.S_IMODE(staged.directory.stat().st_mode) == 0o700
    assert len(staged.paths) == 1
    assert staged.paths[0].parent == staged.directory
    assert len(staged.paths[0].name.encode()) <= 200
    assert re.fullmatch(r"[A-Za-z0-9._-]+", staged.paths[0].name)
    assert stat.S_IMODE(staged.paths[0].stat().st_mode) == 0o600
    assert staged.paths[0].read_bytes() == b"payload"

    staged.cleanup()
    staged.cleanup()

    assert not staged.directory.exists()
    assert predictable.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_filesystem_input_error_uses_safe_typed_boundary(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("occupied", encoding="utf-8")

    with pytest.raises(
        AgentWorkspaceOrAttachmentError,
        match="Discord attachments could not be staged safely",
    ):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [FakeAttachment("one.txt", b"one")])),
            root,
            trigger_event_id=42,
        )

    assert root.read_text(encoding="utf-8") == "occupied"


@pytest.mark.asyncio
async def test_creation_failure_removes_new_private_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"

    def fail_private_mode(_path: Path, _mode: int) -> None:
        raise OSError("mode failure")

    monkeypatch.setattr(discord_task_inputs.os, "chmod", fail_private_mode)

    with pytest.raises(AgentWorkspaceOrAttachmentError):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [FakeAttachment("one.txt", b"one")])),
            root,
            trigger_event_id=42,
        )

    assert root.exists()
    assert list(root.iterdir()) == []
