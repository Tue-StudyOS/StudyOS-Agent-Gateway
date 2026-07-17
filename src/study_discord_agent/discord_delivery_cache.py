from __future__ import annotations

import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.discord_reply_content import (
    MAX_DISCORD_ATTACHMENTS,
    PreparedDiscordReply,
)


class DiscordDeliveryCacheError(RuntimeError):
    """A delivery reply could not be cached with unambiguous ownership."""


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    file_type: int


@dataclass(frozen=True)
class _CachedReply:
    reply: PreparedDiscordReply
    generated_path: Path | None
    generated_identity: _FileIdentity | None


class DiscordDeliveryCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CachedReply] = {}
        self._closed = False
        self._lock = threading.Lock()

    def put(self, task_id: str, reply: PreparedDiscordReply) -> None:
        with self._lock:
            if self._closed:
                raise DiscordDeliveryCacheError("Discord delivery cache is closed")
            if task_id in self._entries:
                raise DiscordDeliveryCacheError("Discord task reply is already cached")
            if not task_id:
                raise DiscordDeliveryCacheError("Discord delivery cache task ID is invalid")
            if len(reply.files) > MAX_DISCORD_ATTACHMENTS:
                raise DiscordDeliveryCacheError(
                    "Discord delivery replies accept at most 10 files"
                )
            generated = reply.generated_file
            if generated is not None and generated not in reply.files:
                raise DiscordDeliveryCacheError(
                    "Generated Discord reply file must be included in reply files"
                )
            generated_path = _absolute_path(generated) if generated is not None else None
            generated_identity = _capture_identity(generated_path)
            self._entries[task_id] = _CachedReply(
                reply=reply,
                generated_path=generated_path,
                generated_identity=generated_identity,
            )

    def consume(
        self,
        task_id: str,
        allowed_roots: tuple[Path, ...],
        max_bytes: int,
    ) -> PreparedDiscordReply | None:
        with self._lock:
            entry = self._entries.pop(task_id, None)
        if entry is None:
            return None

        validated = _validate_entry(entry, allowed_roots, max_bytes)
        if validated is not None:
            return validated
        _delete_owned_generated(entry)
        return None

    def discard(self, task_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(task_id, None)
        if entry is not None:
            _delete_owned_generated(entry)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = tuple(self._entries.values())
            self._entries.clear()
        first_error: DiscordDeliveryCacheError | None = None
        for entry in entries:
            try:
                _delete_owned_generated(entry)
            except DiscordDeliveryCacheError as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


def _validate_entry(
    entry: _CachedReply,
    allowed_roots: tuple[Path, ...],
    max_bytes: int,
) -> PreparedDiscordReply | None:
    reply = entry.reply
    generated = reply.generated_file
    if not allowed_roots or type(max_bytes) is not int or max_bytes < 0:
        return None
    if generated is not None and generated not in reply.files:
        return None
    try:
        resolved_roots = tuple(_validated_root(root) for root in allowed_roots)
        generated_index = reply.files.index(generated) if generated is not None else None
        validated_files: list[Path] = []
        for index, path in enumerate(reply.files):
            absolute = _absolute_path(path)
            status = absolute.lstat()
            if not stat.S_ISREG(status.st_mode):
                return None
            resolved = absolute.resolve(strict=True)
            if not any(resolved.is_relative_to(root) for root in resolved_roots):
                return None
            if status.st_size > max_bytes:
                return None
            if index == generated_index and not _matches_owned_generated(entry, status):
                return None
            validated_files.append(resolved)
    except (OSError, RuntimeError, ValueError):
        return None

    validated_generated = (
        validated_files[generated_index] if generated_index is not None else None
    )
    return PreparedDiscordReply(
        message=reply.message,
        files=tuple(validated_files),
        generated_file=validated_generated,
    )


def _validated_root(root: Path) -> Path:
    resolved = root.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("allowed root is not a directory")
    return resolved


def _matches_owned_generated(entry: _CachedReply, status: os.stat_result) -> bool:
    identity = entry.generated_identity
    return identity is not None and identity == _identity(status)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _capture_identity(path: Path | None) -> _FileIdentity | None:
    if path is None:
        return None
    try:
        return _identity(path.lstat())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DiscordDeliveryCacheError(
            "Generated Discord reply ownership could not be established"
        ) from exc


def _identity(status: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=status.st_dev,
        inode=status.st_ino,
        file_type=stat.S_IFMT(status.st_mode),
    )


def _delete_owned_generated(entry: _CachedReply) -> None:
    path = entry.generated_path
    identity = entry.generated_identity
    if path is None or identity is None:
        return
    try:
        status = path.lstat()
        if _identity(status) != identity or stat.S_ISDIR(status.st_mode):
            return
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DiscordDeliveryCacheError(
            "Generated Discord reply file could not be cleaned up safely"
        ) from exc
