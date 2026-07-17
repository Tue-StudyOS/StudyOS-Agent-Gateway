import errno
import os
import stat
from pathlib import Path
from typing import Any, cast

import pytest
from discord_task_input_fakes import FakeAttachment, FakeMessage

from study_discord_agent import discord_staging_files
from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_task_inputs import stage_message_attachments


class StagingAbort(BaseException):
    pass


@pytest.mark.asyncio
async def test_non_exception_abort_cleans_owned_stage_and_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    abort = StagingAbort("stop now")
    attachment = FakeAttachment("one.txt", b"partial", error_after_write=abort)

    with pytest.raises(StagingAbort) as raised:
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [attachment])),
            root,
            trigger_event_id=42,
        )

    assert raised.value is abort
    assert root.exists()
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_precreated_group_or_other_writable_root_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir(mode=0o700)
    root.chmod(0o777)
    attachment = FakeAttachment("one.txt", b"payload")

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="staged safely"):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [attachment])),
            root,
            trigger_event_id=42,
        )

    assert attachment.save_calls == 0
    assert stat.S_IMODE(root.stat().st_mode) == 0o777
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_fchmod_race_never_changes_unrelated_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir(mode=0o700)
    original_fchmod = os.fchmod
    replacement: Path | None = None
    moved: Path | None = None

    def replace_before_fchmod(file_descriptor: int, mode: int) -> None:
        nonlocal replacement, moved
        candidates = tuple(path for path in root.iterdir() if path.name.startswith("42-"))
        assert len(candidates) == 1
        replacement = candidates[0]
        moved = root / "moved-owned-stage"
        replacement.rename(moved)
        replacement.mkdir(mode=0o755)
        (replacement / "keep.txt").write_text("keep", encoding="utf-8")
        original_fchmod(file_descriptor, mode)

    monkeypatch.setattr(discord_staging_files.os, "fchmod", replace_before_fchmod)

    with pytest.raises(AgentWorkspaceOrAttachmentError):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [FakeAttachment("one.txt", b"one")])),
            root,
            trigger_event_id=42,
        )

    assert replacement is not None
    assert moved is not None
    assert (replacement / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert stat.S_IMODE(replacement.stat().st_mode) == 0o755


@pytest.mark.asyncio
async def test_cleanup_open_race_never_deletes_unrelated_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir(mode=0o700)
    staged = await stage_message_attachments(
        cast(Any, FakeMessage(99, [FakeAttachment("one.txt", b"one")])),
        root,
        trigger_event_id=42,
    )
    assert staged.directory is not None
    staged_directory = staged.directory
    original_open = os.open
    raced = False

    def replace_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal raced
        if not raced and dir_fd is not None and path == staged_directory.name:
            raced = True
            moved = root / "moved-owned-stage"
            staged_directory.rename(moved)
            staged_directory.mkdir(mode=0o700)
            (staged_directory / "keep.txt").write_text("keep", encoding="utf-8")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(discord_staging_files.os, "open", replace_before_open)

    staged.cleanup()

    assert raced
    assert (staged_directory / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_post_open_fstat_failure_closes_directory_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir(mode=0o700)
    original_open = os.open
    original_fstat = os.fstat
    opened: list[int] = []

    def record_directory_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        file_descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and flags & getattr(os, "O_DIRECTORY", 0):
            opened.append(file_descriptor)
        return file_descriptor

    def fail_child_fstat(file_descriptor: int) -> os.stat_result:
        if file_descriptor in opened:
            raise OSError("fstat failed")
        return original_fstat(file_descriptor)

    monkeypatch.setattr(discord_staging_files.os, "open", record_directory_open)
    monkeypatch.setattr(discord_staging_files.os, "fstat", fail_child_fstat)

    with pytest.raises(AgentWorkspaceOrAttachmentError):
        await stage_message_attachments(
            cast(Any, FakeMessage(99, [FakeAttachment("one.txt", b"one")])),
            root,
            trigger_event_id=42,
        )

    assert opened
    for file_descriptor in opened:
        with pytest.raises(OSError) as raised:
            original_fstat(file_descriptor)
        assert raised.value.errno == errno.EBADF
    assert list(root.iterdir()) == []
