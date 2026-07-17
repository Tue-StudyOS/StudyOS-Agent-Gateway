"""Descriptor-owned staging within the trusted gateway-EUID boundary.

POSIX cannot unlink a name conditionally by inode. The owning gateway EUID is therefore
part of the TCB; malicious mutation by another process with that same EUID is out of
scope. Group/other writers are rejected and cleanup never follows entries recursively.
"""

from __future__ import annotations

import os
import secrets
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError

STAGING_ERROR = "Discord attachments could not be staged safely"
_CLEANUP_ERROR = "Staged Discord attachments could not be cleaned up safely"
_DirectoryIdentity = tuple[int, int]


def _new_filename_list() -> list[str]:
    return []


@dataclass
class StagingOwnership:
    root_fd: int
    directory_fd: int
    directory_name: str
    identity: _DirectoryIdentity
    file_names: list[str] = field(default_factory=_new_filename_list)
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_file(self, filename: str) -> None:
        self.file_names.append(filename)

    def entry_is_owned(self) -> bool:
        return _directory_entry_matches(self.root_fd, self.directory_name, self.identity)

    def cleanup(self) -> None:
        with self.lock:
            if self.closed:
                return
            try:
                for filename in self.file_names:
                    with suppress(FileNotFoundError):
                        os.unlink(filename, dir_fd=self.directory_fd)
                if self.entry_is_owned():
                    os.rmdir(self.directory_name, dir_fd=self.root_fd)
                self._close()
            except OSError as exc:
                raise AgentWorkspaceOrAttachmentError(_CLEANUP_ERROR) from exc

    def _close(self) -> None:
        os.close(self.directory_fd)
        os.close(self.root_fd)
        self.closed = True


class StagingCleanupRegistry:
    """Strongly retains descriptor ownership until deferred cleanup succeeds."""

    def __init__(self) -> None:
        self._owners: dict[int, StagingOwnership] = {}
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._owners)

    def register(self, ownership: StagingOwnership) -> None:
        with self._lock:
            self._owners[id(ownership)] = ownership

    def retry_all(self) -> None:
        with self._lock:
            owners = tuple(self._owners.values())
        first_error: BaseException | None = None
        for ownership in owners:
            try:
                ownership.cleanup()
            except BaseException as exc:
                first_error = first_error or exc
            else:
                with self._lock:
                    if self._owners.get(id(ownership)) is ownership:
                        self._owners.pop(id(ownership))
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        """Retry every pending owner, retaining failures for another close call."""
        self.retry_all()


DEFAULT_STAGING_CLEANUPS = StagingCleanupRegistry()


def create_staging_ownership(
    root: Path,
    trigger_event_id: int,
    *,
    cleanup_registry: StagingCleanupRegistry = DEFAULT_STAGING_CLEANUPS,
) -> StagingOwnership:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_fd, root_status = _open_directory(root)
    try:
        _validate_root(root_status)
        directory_name = _create_random_directory(root_fd, trigger_event_id)
        directory_fd = -1
        identity: _DirectoryIdentity | None = None
        try:
            directory_fd, created = _open_directory(directory_name, dir_fd=root_fd)
            identity = (created.st_dev, created.st_ino)
            ownership = StagingOwnership(
                root_fd=root_fd,
                directory_fd=directory_fd,
                directory_name=directory_name,
                identity=identity,
            )
            os.fchmod(directory_fd, 0o700)
            verified = os.fstat(directory_fd)
            if not stat.S_ISDIR(verified.st_mode):
                raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
            if stat.S_IMODE(verified.st_mode) != 0o700:
                raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
            if (verified.st_dev, verified.st_ino) != identity:
                raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
            if not ownership.entry_is_owned():
                raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)
            return ownership
        except BaseException:
            if directory_fd >= 0:
                temporary = StagingOwnership(
                    root_fd=root_fd,
                    directory_fd=directory_fd,
                    directory_name=directory_name,
                    identity=identity or (-1, -1),
                )
                try:
                    temporary.cleanup()
                except BaseException:
                    cleanup_registry.register(temporary)
                    root_fd = -1
            else:
                try:
                    os.rmdir(directory_name, dir_fd=root_fd)
                finally:
                    os.close(root_fd)
            raise
    except BaseException:
        _close_if_open(root_fd)
        raise


def _validate_root(status: os.stat_result) -> None:
    # The gateway EUID is the TCB boundary; group/other mutation must be impossible.
    mode = stat.S_IMODE(status.st_mode)
    owner_access = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or mode & owner_access != owner_access
        or mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)


def _create_random_directory(root_fd: int, trigger_event_id: int) -> str:
    for _ in range(100):
        name = f"{trigger_event_id}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
            return name
        except FileExistsError:
            continue
    raise AgentWorkspaceOrAttachmentError(STAGING_ERROR)


def _directory_entry_matches(
    parent_fd: int,
    name: str,
    identity: _DirectoryIdentity,
) -> bool:
    try:
        entry_fd, status = _open_directory(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return False
    try:
        return (status.st_dev, status.st_ino) == identity
    finally:
        os.close(entry_fd)


def _open_directory(
    path: str | Path,
    *,
    dir_fd: int | None = None,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _no_follow_flags()
    file_descriptor = os.open(path, flags, dir_fd=dir_fd)
    try:
        status = os.fstat(file_descriptor)
        if not stat.S_ISDIR(status.st_mode):
            raise OSError("not a directory")
        return file_descriptor, status
    except BaseException:
        os.close(file_descriptor)
        raise


def _no_follow_flags() -> int:
    return getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _close_if_open(file_descriptor: int) -> None:
    with suppress(OSError):
        os.close(file_descriptor)
