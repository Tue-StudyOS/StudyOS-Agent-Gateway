from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from study_discord_agent.discord_delivery_entries import (
    CachedReply,
    DiscordDeliveryCacheError,
    TransferredReply,
    generated_index,
    snapshot_entry,
    validate_restored_policy,
    validated_delivery_policy,
)
from study_discord_agent.discord_delivery_files import close_resources
from study_discord_agent.discord_delivery_resources import DiscordDeliveryLease
from study_discord_agent.discord_file_descriptors import DeliveryFileError
from study_discord_agent.discord_generated_file import (
    GeneratedFileOwnership,
    open_generated_candidate,
)
from study_discord_agent.discord_reply_content import (
    MAX_DISCORD_ATTACHMENTS,
    PreparedDiscordReply,
)


class DiscordDeliveryCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedReply] = {}
        self._processing: set[str] = set()
        self._reserved_paths: set[tuple[int, int, str]] = set()
        self._reserved_files: set[tuple[int, int]] = set()
        self._transferred: dict[int, TransferredReply] = {}
        self._closed = False
        self._lock = threading.Lock()

    def put(self, task_id: str, reply: PreparedDiscordReply) -> None:
        with self._lock:
            self._validate_put(task_id, reply)
            generated_position = generated_index(reply)
            generated: GeneratedFileOwnership | None = None
            if generated_position is not None:
                generated_file = reply.generated_file
                assert generated_file is not None
                try:
                    candidate = open_generated_candidate(generated_file)
                except DeliveryFileError as exc:
                    raise DiscordDeliveryCacheError(str(exc)) from exc
                try:
                    if (
                        candidate.path_reservation in self._reserved_paths
                        or candidate.file_reservation in self._reserved_files
                    ):
                        raise DiscordDeliveryCacheError(
                            "Generated Discord reply file is already owned"
                        )
                    generated = candidate.quarantine()
                except DeliveryFileError as exc:
                    raise DiscordDeliveryCacheError(str(exc)) from exc
                finally:
                    candidate.close()
                self._reserve(generated)
            self._entries[task_id] = CachedReply(
                reply=reply,
                generated_index=generated_position,
                generated=generated,
            )

    def consume(
        self,
        task_id: str,
        allowed_roots: tuple[Path, ...],
        max_bytes: int,
    ) -> PreparedDiscordReply | None:
        entry = self._claim(task_id)
        if entry is None:
            return None
        try:
            normalized_roots = validated_delivery_policy(allowed_roots, max_bytes)
            if entry.lease is not None:
                validate_restored_policy(entry, normalized_roots, max_bytes)
                entry.lease.activate_for_delivery(
                    lambda: self._remove_claimed(
                        task_id,
                        entry,
                        release_reservation=False,
                    )
                )
                return entry.reply
            paths, resources = snapshot_entry(entry, allowed_roots, max_bytes)
        except DeliveryFileError:
            try:
                self._cleanup_entry(entry)
            except BaseException:
                self._unclaim(task_id)
                raise
            self._remove_claimed(task_id, entry, release_reservation=True)
            return None
        except BaseException:
            self._unclaim(task_id)
            raise

        try:
            lease: DiscordDeliveryLease
            lease = DiscordDeliveryLease(
                files=tuple(resources),
                _release=lambda: self._release_transferred(lease),
            )
            generated_file = (
                paths[entry.generated_index] if entry.generated_index is not None else None
            )
            prepared = PreparedDiscordReply(
                message=entry.reply.message,
                files=tuple(paths),
                generated_file=generated_file,
                delivery_lease=lease,
            )
            transfer = TransferredReply(
                task_id=task_id,
                entry=entry,
                reply=prepared,
                lease=lease,
                allowed_roots=normalized_roots,
                max_bytes=max_bytes,
            )
            self._transfer_claimed(task_id, entry, transfer)
            return prepared
        except BaseException:
            with suppress(BaseException):
                close_resources(resources)
            self._unclaim(task_id)
            raise

    def restore(self, task_id: str, reply: PreparedDiscordReply) -> None:
        lease = reply.delivery_lease
        if lease is None:
            raise DiscordDeliveryCacheError("Discord delivery reply has no active lease")
        lease.reclaim_for_cache(
            lambda: self._restore_transferred(task_id, reply, lease)
        )

    def discard(self, task_id: str) -> None:
        entry = self._claim(task_id)
        if entry is None:
            return
        try:
            self._cleanup_entry(entry)
        except BaseException:
            self._unclaim(task_id)
            raise
        self._remove_claimed(task_id, entry, release_reservation=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            task_ids = tuple(self._entries)
        first_error: BaseException | None = None
        for task_id in task_ids:
            entry = self._claim(task_id)
            if entry is None:
                continue
            try:
                self._cleanup_entry(entry)
            except BaseException as exc:
                self._unclaim(task_id)
                first_error = first_error or exc
            else:
                self._remove_claimed(task_id, entry, release_reservation=True)
        if first_error is not None:
            raise first_error

    def _validate_put(self, task_id: str, reply: PreparedDiscordReply) -> None:
        if self._closed:
            raise DiscordDeliveryCacheError("Discord delivery cache is closed")
        if task_id in self._entries:
            raise DiscordDeliveryCacheError("Discord task reply is already cached")
        if any(transfer.task_id == task_id for transfer in self._transferred.values()):
            raise DiscordDeliveryCacheError("Discord task reply is already in flight")
        if not task_id:
            raise DiscordDeliveryCacheError("Discord delivery cache task ID is invalid")
        if len(reply.files) > MAX_DISCORD_ATTACHMENTS:
            raise DiscordDeliveryCacheError("Discord delivery replies accept at most 10 files")
        if reply.delivery_lease is not None:
            raise DiscordDeliveryCacheError("Discord delivery reply already has an active lease")

    def _claim(self, task_id: str) -> CachedReply | None:
        with self._lock:
            if task_id in self._processing:
                return None
            entry = self._entries.get(task_id)
            if entry is not None:
                self._processing.add(task_id)
            return entry

    def _unclaim(self, task_id: str) -> None:
        with self._lock:
            self._processing.discard(task_id)

    def _remove_claimed(
        self,
        task_id: str,
        entry: CachedReply,
        *,
        release_reservation: bool,
    ) -> None:
        with self._lock:
            if self._entries.get(task_id) is entry:
                self._entries.pop(task_id)
            self._processing.discard(task_id)
            if release_reservation and entry.generated is not None:
                self._release_reservation(entry.generated)

    def _transfer_claimed(
        self,
        task_id: str,
        entry: CachedReply,
        transfer: TransferredReply,
    ) -> None:
        with self._lock:
            if self._entries.get(task_id) is not entry:
                raise DiscordDeliveryCacheError("Discord delivery cache claim was lost")
            lease_id = id(transfer.lease)
            try:
                self._transferred[lease_id] = transfer
                self._entries.pop(task_id)
                self._processing.discard(task_id)
            except BaseException:
                self._transferred.pop(lease_id, None)
                self._entries[task_id] = entry
                raise

    def _restore_transferred(
        self,
        task_id: str,
        reply: PreparedDiscordReply,
        lease: DiscordDeliveryLease,
    ) -> None:
        with self._lock:
            transfer = self._transferred.get(id(lease))
            if transfer is None or transfer.lease is not lease:
                raise DiscordDeliveryCacheError(
                    "Discord delivery lease does not belong to this cache"
                )
            if task_id != transfer.task_id:
                raise DiscordDeliveryCacheError(
                    "Discord delivery lease belongs to its original task"
                )
            if transfer.reply is not reply:
                raise DiscordDeliveryCacheError(
                    "Discord cache restore requires the exact in-flight reply"
                )
            if self._closed:
                raise DiscordDeliveryCacheError("Discord delivery cache is closed")
            if task_id in self._entries or task_id in self._processing:
                raise DiscordDeliveryCacheError("Discord task reply is already cached")
            self._entries[task_id] = replace(
                transfer.entry,
                reply=reply,
                lease=lease,
                allowed_roots=transfer.allowed_roots,
                max_bytes=transfer.max_bytes,
            )

    def _reserve(self, generated: GeneratedFileOwnership) -> None:
        self._reserved_paths.add(generated.path_reservation)
        self._reserved_files.add(generated.file_reservation)

    def _release_reservation(self, generated: GeneratedFileOwnership) -> None:
        self._reserved_paths.discard(generated.path_reservation)
        self._reserved_files.discard(generated.file_reservation)

    def _cleanup_entry(self, entry: CachedReply) -> None:
        if entry.lease is not None:
            entry.lease.close_from_cache()
            return
        if entry.generated is not None:
            entry.generated.cleanup()

    def _release_transferred(self, lease: DiscordDeliveryLease) -> None:
        with self._lock:
            transfer = self._transferred.get(id(lease))
            if transfer is None or transfer.lease is not lease:
                raise DiscordDeliveryCacheError("Discord delivery lease ownership was lost")
        if transfer.entry.generated is not None:
            transfer.entry.generated.cleanup()
        with self._lock:
            current = self._transferred.get(id(lease))
            if current is transfer:
                self._transferred.pop(id(lease))
                if transfer.entry.generated is not None:
                    self._release_reservation(transfer.entry.generated)
