from __future__ import annotations

import os
import stat
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError
from study_discord_agent.discord_files import sanitize_filename
from study_discord_agent.discord_staging_files import (
    STAGING_ERROR,
    StagingOwnership,
    create_staging_ownership,
)

if TYPE_CHECKING:
    import discord

MAX_DISCORD_INPUT_ATTACHMENTS = 10
MAX_DISCORD_INPUT_ATTACHMENT_BYTES = 8_000_000
MAX_STAGED_FILENAME_BYTES = 200

@dataclass(frozen=True)
class StagedDiscordAttachments:
    paths: tuple[Path, ...]
    directory: Path | None
    _ownership: StagingOwnership | None = field(default=None, repr=False, compare=False)

    def cleanup(self) -> None:
        if self._ownership is not None:
            self._ownership.cleanup()


async def stage_message_attachments(
    message: discord.Message,
    root: Path,
    *,
    trigger_event_id: int,
) -> StagedDiscordAttachments:
    attachments, filenames = _validated_attachment_metadata(message, trigger_event_id)
    if not attachments:
        return StagedDiscordAttachments(paths=(), directory=None)

    ownership: StagingOwnership | None = None
    try:
        root = root.expanduser()
        ownership = create_staging_ownership(root, trigger_event_id)
        directory = root / ownership.directory_name
        paths = await _save_attachments(attachments, filenames, directory, ownership)
        if not ownership.entry_is_owned():
            raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
        return StagedDiscordAttachments(
            paths=paths,
            directory=directory,
            _ownership=ownership,
        )
    except BaseException as exc:
        if ownership is not None:
            with suppress(BaseException):
                ownership.cleanup()
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, AgentWorkspaceOrAttachmentError):
            raise
        raise AgentWorkspaceOrAttachmentError(STAGING_ERROR) from exc


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
            filenames.append(_bounded_filename(index, _validated_filename(attachment.filename)))
        return attachments, tuple(filenames)
    except AgentWorkspaceOrAttachmentError:
        raise
    except Exception as exc:
        raise AgentWorkspaceOrAttachmentError(STAGING_ERROR) from exc


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


async def _save_attachments(
    attachments: tuple[discord.Attachment, ...],
    filenames: tuple[str, ...],
    directory: Path,
    ownership: StagingOwnership,
) -> tuple[Path, ...]:
    saved: list[Path] = []
    for attachment, filename in zip(attachments, filenames, strict=True):
        await _save_attachment(attachment, filename, ownership)
        saved.append(directory / filename)
    return tuple(saved)


async def _save_attachment(
    attachment: discord.Attachment,
    filename: str,
    ownership: StagingOwnership,
) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | _no_follow_flags()
    file_fd = os.open(filename, flags, 0o600, dir_fd=ownership.directory_fd)
    ownership.add_file(filename)
    try:
        status = os.fstat(file_fd)
        if not stat.S_ISREG(status.st_mode):
            raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
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


def _no_follow_flags() -> int:
    return getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
