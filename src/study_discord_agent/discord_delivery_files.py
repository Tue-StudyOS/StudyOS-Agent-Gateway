from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO

from study_discord_agent.discord_delivery_resources import PinnedDiscordFile
from study_discord_agent.discord_file_descriptors import (
    DeliveryFileError,
    absolute_path,
    open_file_beneath_roots,
)


def snapshot_allowed_file(
    path: Path,
    allowed_roots: tuple[Path, ...],
    max_bytes: int,
) -> PinnedDiscordFile:
    absolute = absolute_path(path)
    descriptor = open_file_beneath_roots(absolute, allowed_roots)
    return snapshot_descriptor(descriptor, absolute, absolute.name, max_bytes)


def close_resources(resources: list[PinnedDiscordFile]) -> None:
    first_error: BaseException | None = None
    for resource in resources:
        try:
            resource.stream.close()
        except BaseException as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


def snapshot_descriptor(
    descriptor: int,
    source_path: Path,
    filename: str,
    max_bytes: int,
) -> PinnedDiscordFile:
    snapshot: BinaryIO | None = None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > max_bytes:
            raise DeliveryFileError("Discord delivery file failed type or size validation")
        # Ownership intentionally escapes this function into DiscordDeliveryLease.
        snapshot = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115
        os.lseek(descriptor, 0, os.SEEK_SET)
        total = 0
        while chunk := os.read(descriptor, 64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise DeliveryFileError("Discord delivery file exceeded its size limit")
            snapshot.write(chunk)
        snapshot.seek(0)
        return PinnedDiscordFile(
            source_path=source_path,
            filename=filename,
            size=total,
            stream=snapshot,
        )
    except BaseException:
        if snapshot is not None:
            snapshot.close()
        raise
    finally:
        os.close(descriptor)
