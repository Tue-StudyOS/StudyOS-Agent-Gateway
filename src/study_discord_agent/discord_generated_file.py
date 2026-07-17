from __future__ import annotations

import os
import secrets
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from study_discord_agent.discord_delivery_files import snapshot_descriptor
from study_discord_agent.discord_delivery_resources import PinnedDiscordFile
from study_discord_agent.discord_file_descriptors import (
    DeliveryFileError,
    Identity,
    absolute_path,
    close_if_open,
    directory_entry_matches,
    directory_identity_is_allowed,
    identity,
    open_directory,
    open_regular,
)

PathReservation = tuple[int, int, str]
FileReservation = tuple[int, int]
_QUARANTINE_FILE = "generated"


@dataclass
class GeneratedCandidate:
    original_path: Path
    parent_path: Path
    parent_fd: int
    file_fd: int
    parent_identity: Identity
    file_identity: Identity
    closed: bool = False

    @property
    def path_reservation(self) -> PathReservation:
        return (*self.parent_identity, self.original_path.name)

    @property
    def file_reservation(self) -> FileReservation:
        return self.file_identity

    def quarantine(self) -> GeneratedFileOwnership:
        directory_name = _create_quarantine_directory(self.parent_fd)
        directory_fd = -1
        moved = False
        try:
            directory_fd, created = open_directory(directory_name, dir_fd=self.parent_fd)
            directory_identity = identity(created)
            os.fchmod(directory_fd, 0o700)
            verified = os.fstat(directory_fd)
            if identity(verified) != directory_identity:
                raise DeliveryFileError("Generated Discord reply quarantine is unsafe")
            if stat.S_IMODE(verified.st_mode) != 0o700:
                raise DeliveryFileError("Generated Discord reply quarantine is unsafe")
            os.rename(
                self.original_path.name,
                _QUARANTINE_FILE,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=directory_fd,
            )
            moved = True
            check_fd, quarantined = open_regular(_QUARANTINE_FILE, dir_fd=directory_fd)
            os.close(check_fd)
            if identity(quarantined) != self.file_identity:
                raise DeliveryFileError("Generated Discord reply changed before ownership")
            ownership = GeneratedFileOwnership(
                original_path=self.original_path,
                parent_path=self.parent_path,
                parent_fd=self.parent_fd,
                file_fd=self.file_fd,
                directory_name=directory_name,
                directory_fd=directory_fd,
                directory_identity=directory_identity,
                parent_identity=self.parent_identity,
                file_identity=self.file_identity,
            )
            self.parent_fd = -1
            self.file_fd = -1
            self.closed = True
            return ownership
        except BaseException:
            if moved and directory_fd >= 0:
                _restore_quarantined_file(
                    directory_fd,
                    self.parent_fd,
                    self.original_path.name,
                )
            close_if_open(directory_fd)
            with suppress(OSError):
                os.rmdir(directory_name, dir_fd=self.parent_fd)
            self.close()
            raise

    def close(self) -> None:
        if self.closed:
            return
        close_if_open(self.file_fd)
        close_if_open(self.parent_fd)
        self.closed = True


@dataclass
class GeneratedFileOwnership:
    original_path: Path
    parent_path: Path
    parent_fd: int
    file_fd: int
    directory_name: str
    directory_fd: int
    directory_identity: Identity
    parent_identity: Identity
    file_identity: Identity
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def quarantine_path(self) -> Path:
        return self.parent_path / self.directory_name / _QUARANTINE_FILE

    @property
    def path_reservation(self) -> PathReservation:
        return (*self.parent_identity, self.original_path.name)

    @property
    def file_reservation(self) -> FileReservation:
        return self.file_identity

    def parent_is_allowed(self, allowed_roots: tuple[Path, ...]) -> bool:
        return directory_identity_is_allowed(
            self.parent_path,
            self.parent_identity,
            allowed_roots,
        )

    def snapshot(self, max_bytes: int) -> PinnedDiscordFile:
        return snapshot_descriptor(
            os.dup(self.file_fd),
            self.quarantine_path,
            self.original_path.name,
            max_bytes,
        )

    def cleanup(self) -> None:
        with self.lock:
            if self.closed:
                return
            with suppress(FileNotFoundError):
                os.unlink(_QUARANTINE_FILE, dir_fd=self.directory_fd)
            if directory_entry_matches(
                self.parent_fd,
                self.directory_name,
                self.directory_identity,
            ):
                os.rmdir(self.directory_name, dir_fd=self.parent_fd)
            close_if_open(self.file_fd)
            close_if_open(self.directory_fd)
            close_if_open(self.parent_fd)
            self.closed = True


def open_generated_candidate(path: Path) -> GeneratedCandidate:
    original_path = absolute_path(path)
    parent_path = original_path.parent
    try:
        parent_fd, parent_status = open_directory(parent_path)
    except OSError as exc:
        raise DeliveryFileError("Generated Discord reply parent is unsafe") from exc
    file_fd = -1
    try:
        _validate_owned_parent(parent_status)
        try:
            file_fd, file_status = open_regular(original_path.name, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise DeliveryFileError("Generated Discord reply file does not exist") from exc
        except OSError as exc:
            raise DeliveryFileError("Generated Discord reply must be a regular file") from exc
        return GeneratedCandidate(
            original_path=original_path,
            parent_path=parent_path,
            parent_fd=parent_fd,
            file_fd=file_fd,
            parent_identity=identity(parent_status),
            file_identity=identity(file_status),
        )
    except BaseException:
        close_if_open(file_fd)
        close_if_open(parent_fd)
        raise


def _validate_owned_parent(status: os.stat_result) -> None:
    mode = stat.S_IMODE(status.st_mode)
    owner_access = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    if (
        status.st_uid != os.geteuid()
        or mode & owner_access != owner_access
        or mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise DeliveryFileError("Generated Discord reply parent is unsafe")


def _create_quarantine_directory(parent_fd: int) -> str:
    for _ in range(100):
        name = f".studyos-delivery-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            return name
        except FileExistsError:
            continue
    raise DeliveryFileError("Generated Discord reply quarantine could not be created")


def _restore_quarantined_file(
    directory_fd: int,
    parent_fd: int,
    original_name: str,
) -> None:
    try:
        os.link(
            _QUARANTINE_FILE,
            original_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return
    with suppress(OSError):
        os.unlink(_QUARANTINE_FILE, dir_fd=directory_fd)
