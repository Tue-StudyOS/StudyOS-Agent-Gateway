from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.discord_delivery_files import (
    close_resources,
    snapshot_allowed_file,
)
from study_discord_agent.discord_delivery_resources import (
    DiscordDeliveryLease,
    PinnedDiscordFile,
)
from study_discord_agent.discord_file_descriptors import DeliveryFileError, absolute_path
from study_discord_agent.discord_generated_file import GeneratedFileOwnership
from study_discord_agent.discord_reply_content import PreparedDiscordReply


class DiscordDeliveryCacheError(RuntimeError):
    """A delivery reply could not be cached with unambiguous ownership."""


@dataclass(frozen=True)
class CachedReply:
    reply: PreparedDiscordReply
    generated_index: int | None
    generated: GeneratedFileOwnership | None
    lease: DiscordDeliveryLease | None = None
    allowed_roots: tuple[Path, ...] | None = None
    max_bytes: int | None = None


@dataclass(frozen=True)
class TransferredReply:
    task_id: str
    entry: CachedReply
    reply: PreparedDiscordReply
    lease: DiscordDeliveryLease
    allowed_roots: tuple[Path, ...]
    max_bytes: int


def generated_index(reply: PreparedDiscordReply) -> int | None:
    generated = reply.generated_file
    if generated is None:
        return None
    matches = tuple(index for index, path in enumerate(reply.files) if path is generated)
    if len(matches) != 1:
        raise DiscordDeliveryCacheError(
            "Generated Discord reply must be the same reply-file object exactly once"
        )
    return matches[0]


def validated_delivery_policy(
    allowed_roots: tuple[Path, ...],
    max_bytes: int,
) -> tuple[Path, ...]:
    if not allowed_roots or type(max_bytes) is not int or max_bytes < 0:
        raise DeliveryFileError("Discord delivery roots or size limit are invalid")
    try:
        return tuple(absolute_path(root) for root in allowed_roots)
    except (OSError, TypeError, ValueError) as exc:
        raise DeliveryFileError("Discord delivery roots or size limit are invalid") from exc


def validate_restored_policy(
    entry: CachedReply,
    allowed_roots: tuple[Path, ...],
    max_bytes: int,
) -> None:
    if entry.allowed_roots != allowed_roots or entry.max_bytes != max_bytes:
        raise DeliveryFileError("Discord retry delivery policy changed")
    lease = entry.lease
    if lease is None or any(resource.size > max_bytes for resource in lease.files):
        raise DeliveryFileError("Discord retry delivery resources are invalid")
    if entry.generated is not None and not entry.generated.parent_is_allowed(allowed_roots):
        raise DeliveryFileError("Generated Discord reply is outside allowed roots")


def snapshot_entry(
    entry: CachedReply,
    allowed_roots: tuple[Path, ...],
    max_bytes: int,
) -> tuple[list[Path], list[PinnedDiscordFile]]:
    paths: list[Path] = []
    resources: list[PinnedDiscordFile] = []
    try:
        for index, path in enumerate(entry.reply.files):
            if index == entry.generated_index:
                generated = entry.generated
                if generated is None or not generated.parent_is_allowed(allowed_roots):
                    raise DeliveryFileError("Generated Discord reply is outside allowed roots")
                resource = generated.snapshot(max_bytes)
                paths.append(generated.quarantine_path)
            else:
                resource = snapshot_allowed_file(path, allowed_roots, max_bytes)
                paths.append(resource.source_path)
            resources.append(resource)
        return paths, resources
    except BaseException:
        close_resources(resources)
        raise
