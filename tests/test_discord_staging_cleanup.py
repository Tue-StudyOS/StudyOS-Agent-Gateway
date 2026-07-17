import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from study_discord_agent import discord_staging_files
from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_staging_files import StagingCleanupRegistry
from study_discord_agent.discord_task_inputs import (
    StagedDiscordAttachments,
    stage_message_attachments,
)
from tests.discord_task_input_fakes import (
    FakeAttachment,
    FakeAttachmentDownloader,
    FakeMessage,
)


def _fail_unlink(*_args: object, **_kwargs: object) -> None:
    raise OSError("unlink failed")


def _fail_rmdir(*_args: object, **_kwargs: object) -> None:
    raise OSError("rmdir failed")


class StagingAbort(BaseException):
    pass


async def _stage(
    attachment: FakeAttachment,
    root: Path,
    registry: StagingCleanupRegistry,
) -> StagedDiscordAttachments:
    return await stage_message_attachments(
        cast(Any, FakeMessage(99, [attachment])),
        root,
        trigger_event_id=42,
        downloader=FakeAttachmentDownloader(),
        cleanup_registry=registry,
    )


@pytest.mark.asyncio
async def test_unlink_failure_retains_strong_owner_for_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    registry = StagingCleanupRegistry()
    abort = StagingAbort("cancel operation")
    attachment = FakeAttachment("one.txt", b"partial", error_after_write=abort)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            discord_staging_files.os,
            "unlink",
            _fail_unlink,
        )
        with pytest.raises(StagingAbort) as raised:
            await _stage(attachment, root, registry)
        assert raised.value is abort

    assert registry.pending_count == 1
    assert any(root.iterdir())
    registry.retry_all()
    assert registry.pending_count == 0
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_rmdir_failure_is_registered_and_close_retries_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    registry = StagingCleanupRegistry()
    attachment = FakeAttachment(
        "one.txt",
        b"partial",
        error_after_write=OSError("private network detail"),
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(
            discord_staging_files.os,
            "rmdir",
            _fail_rmdir,
        )
        with pytest.raises(AgentWorkspaceOrAttachmentError) as raised:
            await _stage(attachment, root, registry)

    assert "private network detail" not in str(raised.value)
    assert registry.pending_count == 1
    registry.close()
    assert registry.pending_count == 0
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_explicit_cleanup_failure_registers_stage_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    registry = StagingCleanupRegistry()
    staged = await _stage(FakeAttachment("one.txt", b"saved"), root, registry)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            discord_staging_files.os,
            "unlink",
            _fail_unlink,
        )
        with pytest.raises(AgentWorkspaceOrAttachmentError, match="cleaned up safely"):
            staged.cleanup()

    assert registry.pending_count == 1
    registry.retry_all()
    assert registry.pending_count == 0
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_cancelled_download_with_cleanup_failure_preserves_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    registry = StagingCleanupRegistry()
    cancelled = asyncio.CancelledError()
    attachment = FakeAttachment("one.txt", b"partial", error_after_write=cancelled)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            discord_staging_files.os,
            "unlink",
            _fail_unlink,
        )
        with pytest.raises(asyncio.CancelledError) as raised:
            await _stage(attachment, root, registry)
        assert raised.value is cancelled

    assert registry.pending_count == 1
    registry.close()
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_creation_rollback_failure_retains_owner_for_close_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    registry = StagingCleanupRegistry()
    attachment = FakeAttachment("one.txt", b"payload")

    with monkeypatch.context() as scoped:
        scoped.setattr(discord_staging_files.os, "fchmod", _fail_rmdir)
        scoped.setattr(discord_staging_files.os, "rmdir", _fail_rmdir)
        with pytest.raises(AgentWorkspaceOrAttachmentError, match="staged safely"):
            await _stage(attachment, root, registry)

    assert registry.pending_count == 1
    assert len(list(root.iterdir())) == 1
    registry.close()
    assert registry.pending_count == 0
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_staging_rejects_root_owned_by_another_os_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "attachments"
    root.mkdir(mode=0o700)
    attachment = FakeAttachment("one.txt", b"payload")
    actual_uid = root.stat().st_uid
    monkeypatch.setattr(discord_staging_files.os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="staged safely"):
        await _stage(attachment, root, StagingCleanupRegistry())

    assert attachment.save_calls == 0
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_staging_rejects_symlink_root_without_following_contents(
    tmp_path: Path,
) -> None:
    target = tmp_path / "other-user-root"
    target.mkdir(mode=0o700)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    root = tmp_path / "attachments"
    root.symlink_to(target, target_is_directory=True)
    attachment = FakeAttachment("one.txt", b"payload")

    with pytest.raises(AgentWorkspaceOrAttachmentError, match="staged safely"):
        await _stage(attachment, root, StagingCleanupRegistry())

    assert attachment.save_calls == 0
    assert root.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
