from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

Identity = tuple[int, int]


class DeliveryFileError(RuntimeError):
    """A reply file failed safe descriptor validation."""

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def identity(status: os.stat_result) -> Identity:
    return status.st_dev, status.st_ino


def open_directory(
    path: str | Path,
    *,
    dir_fd: int | None = None,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow_flags()
    descriptor = os.open(path, flags, dir_fd=dir_fd)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISDIR(status.st_mode):
            raise OSError("not a directory")
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        raise


def open_regular(name: str, *, dir_fd: int) -> tuple[int, os.stat_result]:
    descriptor = os.open(name, os.O_RDONLY | no_follow_flags(), dir_fd=dir_fd)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise OSError("not a regular file")
        return descriptor, status
    except BaseException:
        os.close(descriptor)
        raise


def directory_entry_matches(parent_fd: int, name: str, expected: Identity) -> bool:
    try:
        descriptor, status = open_directory(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return False
    os.close(descriptor)
    return identity(status) == expected


def directory_identity_is_allowed(
    path: Path,
    expected: Identity,
    allowed_roots: tuple[Path, ...],
) -> bool:
    for root in allowed_roots:
        root_path = absolute_path(root)
        try:
            relative = path.relative_to(root_path)
        except ValueError:
            continue
        try:
            directory_fd, status = open_relative_directory(root_path, relative.parts)
        except OSError:
            continue
        os.close(directory_fd)
        if identity(status) == expected:
            return True
    return False


def open_file_beneath_roots(path: Path, allowed_roots: tuple[Path, ...]) -> int:
    for root in allowed_roots:
        root_path = absolute_path(root)
        try:
            relative = path.relative_to(root_path)
        except ValueError:
            continue
        if not relative.parts:
            continue
        try:
            return _open_relative_file(root_path, relative.parts)
        except OSError:
            continue
    raise DeliveryFileError("Discord delivery file is outside safe allowed roots")


def open_relative_directory(
    root: Path,
    parts: tuple[str, ...],
) -> tuple[int, os.stat_result]:
    current_fd, status = open_directory(root)
    try:
        for part in parts:
            next_fd, status = open_directory(part, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, status
    except BaseException:
        close_if_open(current_fd)
        raise


def close_if_open(descriptor: int) -> None:
    with suppress(OSError):
        os.close(descriptor)


def no_follow_flags() -> int:
    return getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _open_relative_file(root: Path, parts: tuple[str, ...]) -> int:
    current_fd, _ = open_directory(root)
    try:
        for part in parts[:-1]:
            next_fd, _ = open_directory(part, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd, _ = open_regular(parts[-1], dir_fd=current_fd)
        return file_fd
    finally:
        close_if_open(current_fd)
