from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_files import sanitize_filename

if TYPE_CHECKING:
    import discord

MAX_DISCORD_INPUT_ATTACHMENTS = 10
MAX_DISCORD_INPUT_ATTACHMENT_BYTES = 8_000_000
MAX_STAGED_FILENAME_BYTES = 200

_STAGING_ERROR = "Discord attachments could not be staged safely"
_CLEANUP_ERROR = "Staged Discord attachments could not be cleaned up safely"
_DirectoryIdentity = tuple[int, int]


@dataclass(frozen=True)
class StagedDiscordAttachments:
    paths: tuple[Path, ...]
    directory: Path | None
    _directory_identity: _DirectoryIdentity | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def cleanup(self) -> None:
        directory = self.directory
        identity = self._directory_identity
        if directory is None or identity is None:
            return
        try:
            _remove_owned_directory(directory, identity)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AgentWorkspaceOrAttachmentError(_CLEANUP_ERROR) from exc


async def stage_message_attachments(
    message: discord.Message,
    root: Path,
    *,
    trigger_event_id: int,
) -> StagedDiscordAttachments:
    attachments, filenames = _validated_attachment_metadata(message, trigger_event_id)
    if not attachments:
        return StagedDiscordAttachments(paths=(), directory=None)

    staged: StagedDiscordAttachments | None = None
    try:
        directory, identity = _create_private_directory(root, trigger_event_id)
        staged = StagedDiscordAttachments(
            paths=(),
            directory=directory,
            _directory_identity=identity,
        )
        paths = await _save_attachments(attachments, filenames, directory, identity)
        return StagedDiscordAttachments(
            paths=paths,
            directory=directory,
            _directory_identity=identity,
        )
    except asyncio.CancelledError:
        if staged is not None:
            with contextlib.suppress(AgentWorkspaceOrAttachmentError):
                staged.cleanup()
        raise
    except AgentWorkspaceOrAttachmentError:
        _cleanup_failed_stage(staged)
        raise
    except Exception as exc:
        _cleanup_failed_stage(staged)
        raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR) from exc


def _validated_attachment_metadata(
    message: discord.Message,
    trigger_event_id: int,
) -> tuple[tuple[discord.Attachment, ...], tuple[str, ...]]:
    try:
        attachments = tuple(message.attachments)
        if not attachments:
            return (), ()
        if type(trigger_event_id) is not int or not 0 < trigger_event_id < 2**64:
            raise AgentWorkspaceOrAttachmentError("Discord attachment trigger ID is invalid")
        if len(attachments) > MAX_DISCORD_INPUT_ATTACHMENTS:
            raise AgentWorkspaceOrAttachmentError(
                "Discord tasks accept at most 10 input attachments"
            )

        filenames: list[str] = []
        for index, attachment in enumerate(attachments, start=1):
            size = attachment.size
            if type(size) is not int or size < 0:
                raise AgentWorkspaceOrAttachmentError(
                    "A Discord input attachment has an invalid declared size"
                )
            if size > MAX_DISCORD_INPUT_ATTACHMENT_BYTES:
                raise AgentWorkspaceOrAttachmentError(
                    "Discord input attachments must be at most 8,000,000 bytes each"
                )
            filename = _validated_filename(attachment.filename)
            filenames.append(_bounded_filename(index, filename))
        return attachments, tuple(filenames)
    except AgentWorkspaceOrAttachmentError:
        raise
    except Exception as exc:
        raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR) from exc


def _bounded_filename(index: int, filename: str) -> str:
    prefix = f"{index}_"
    byte_budget = MAX_STAGED_FILENAME_BYTES - len(prefix)
    cleaned = sanitize_filename(filename)[:byte_budget].rstrip("._")
    return f"{prefix}{cleaned or 'attachment'}"


def _validated_filename(filename: object) -> str:
    if not isinstance(filename, str):
        raise AgentWorkspaceOrAttachmentError(
            "A Discord input attachment has an invalid filename"
        )
    return filename


def _create_private_directory(root: Path, trigger_event_id: int) -> tuple[Path, _DirectoryIdentity]:
    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    root_status = root.lstat()
    if not stat.S_ISDIR(root_status.st_mode):
        raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR)

    directory = Path(tempfile.mkdtemp(prefix=f"{trigger_event_id}-", dir=root))
    identity: _DirectoryIdentity | None = None
    try:
        created = directory.lstat()
        identity = (created.st_dev, created.st_ino)
        os.chmod(directory, 0o700)
        status = directory.lstat()
        if not stat.S_ISDIR(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o700:
            raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR)
        if (status.st_dev, status.st_ino) != identity:
            raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR)
        return directory, identity
    except Exception:
        if identity is None:
            shutil.rmtree(directory)
        else:
            _remove_owned_directory(directory, identity)
        raise


async def _save_attachments(
    attachments: tuple[discord.Attachment, ...],
    filenames: tuple[str, ...],
    directory: Path,
    identity: _DirectoryIdentity,
) -> tuple[Path, ...]:
    directory_fd = _open_owned_directory(directory, identity)
    saved: list[Path] = []
    try:
        for attachment, filename in zip(attachments, filenames, strict=True):
            await _save_attachment(attachment, filename, directory_fd)
            saved.append(directory / filename)
    finally:
        os.close(directory_fd)
    return tuple(saved)


def _open_owned_directory(directory: Path, identity: _DirectoryIdentity) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(directory, flags)
    status = os.fstat(directory_fd)
    if not stat.S_ISDIR(status.st_mode) or (status.st_dev, status.st_ino) != identity:
        os.close(directory_fd)
        raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR)
    return directory_fd


async def _save_attachment(
    attachment: discord.Attachment,
    filename: str,
    directory_fd: int,
) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
    try:
        status = os.fstat(file_fd)
        if not stat.S_ISREG(status.st_mode):
            raise AgentWorkspaceOrAttachmentError(_STAGING_ERROR)
        os.fchmod(file_fd, 0o600)
        with os.fdopen(file_fd, "w+b") as output:
            file_fd = -1
            await attachment.save(output)
            output.flush()
            actual_size = os.fstat(output.fileno()).st_size
        if actual_size > MAX_DISCORD_INPUT_ATTACHMENT_BYTES:
            raise AgentWorkspaceOrAttachmentError(
                "Discord input attachments must be at most 8,000,000 bytes each"
            )
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _cleanup_failed_stage(staged: StagedDiscordAttachments | None) -> None:
    if staged is not None:
        staged.cleanup()


def _remove_owned_directory(directory: Path, identity: _DirectoryIdentity) -> None:
    current = directory.lstat()
    if not stat.S_ISDIR(current.st_mode):
        return
    if (current.st_dev, current.st_ino) != identity:
        return
    shutil.rmtree(directory)
