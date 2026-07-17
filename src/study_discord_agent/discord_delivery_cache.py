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
    validate_cache_put,
    validate_restored_policy,
    validated_delivery_policy,
)
from study_discord_agent.discord_delivery_files import close_resources
from study_discord_agent.discord_delivery_resources import (
    DiscordDeliveryLease,
    DiscordDeliveryLeaseError,
)
from study_discord_agent.discord_file_descriptors import DeliveryFileError
from study_discord_agent.discord_generated_registry import GeneratedOwnershipRegistry
from study_discord_agent.discord_reply_content import PreparedDiscordReply


class DiscordDeliveryCache:
    def __init__(self) -> None:
        self._entries: dict[str, CachedReply] = {}
        self._processing: set[str] = set()
        self._transferred: dict[int, TransferredReply] = {}
        self._ownership = GeneratedOwnershipRegistry()
        self._closed = False
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def put(self, task_id: str, reply: PreparedDiscordReply) -> None:
        with self._lock:
            validate_cache_put(
                task_id, reply, self._closed, self._entries, self._transferred
            )
            generated_position = generated_index(reply)
            generated = None
            if generated_position is not None:
                generated_file = reply.generated_file
                assert generated_file is not None
                generated = self._ownership.acquire(generated_file)
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
            except Exception as exc:
                self._unclaim(task_id)
                raise DiscordDeliveryCacheError(
                    "Discord delivery files could not be cleaned up safely"
                ) from exc
            except BaseException:
                self._unclaim(task_id)
                raise
            self._remove_claimed(task_id, entry, release_reservation=True)
            return None
        except Exception as exc:
            self._unclaim(task_id)
            raise DiscordDeliveryCacheError(
                "Discord delivery files could not be prepared safely"
            ) from exc
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
        try:
            lease.reclaim_for_cache(
                lambda: self._restore_transferred(task_id, reply, lease)
            )
        except (DiscordDeliveryCacheError, DiscordDeliveryLeaseError):
            raise
        except Exception as exc:
            raise DiscordDeliveryCacheError(
                "Discord delivery reply could not be restored safely"
            ) from exc

    def discard(self, task_id: str) -> None:
        entry = self._claim(task_id)
        if entry is None:
            return
        try:
            self._cleanup_entry(entry)
        except Exception as exc:
            self._unclaim(task_id)
            raise DiscordDeliveryCacheError(
                "Discord delivery files could not be cleaned up safely"
            ) from exc
        except BaseException:
            self._unclaim(task_id)
            raise
        self._remove_claimed(task_id, entry, release_reservation=True)

    def close(self) -> None:
        """Drain cached work; active transferred leases remain caller-owned."""
        with self._condition:
            self._closed = True
            while self._processing:
                self._condition.wait()
            task_ids = tuple(self._entries)
        first_error: BaseException | None = None
        for task_id in task_ids:
            entry = self._claim(task_id, allow_closed=True)
            if entry is None:
                continue
            try:
                self._cleanup_entry(entry)
            except BaseException as exc:
                self._unclaim(task_id)
                first_error = first_error or exc
            else:
                self._remove_claimed(task_id, entry, release_reservation=True)
        pending_error = self._ownership.drain_pending()
        first_error = first_error or pending_error
        if isinstance(first_error, Exception) and not isinstance(
            first_error,
            DiscordDeliveryCacheError,
        ):
            raise DiscordDeliveryCacheError(
                "Discord delivery files could not be cleaned up safely"
            ) from first_error
        if first_error is not None:
            raise first_error

    def _claim(self, task_id: str, *, allow_closed: bool = False) -> CachedReply | None:
        with self._lock:
            if (self._closed and not allow_closed) or task_id in self._processing:
                return None
            entry = self._entries.get(task_id)
            if entry is not None:
                self._processing.add(task_id)
            return entry

    def _unclaim(self, task_id: str) -> None:
        with self._condition:
            self._processing.discard(task_id)
            self._condition.notify_all()

    def _remove_claimed(
        self,
        task_id: str,
        entry: CachedReply,
        *,
        release_reservation: bool,
    ) -> None:
        with self._condition:
            if self._entries.get(task_id) is entry:
                self._entries.pop(task_id)
            self._processing.discard(task_id)
            if release_reservation and entry.generated is not None:
                self._ownership.release(entry.generated)
            self._condition.notify_all()

    def _transfer_claimed(
        self,
        task_id: str,
        entry: CachedReply,
        transfer: TransferredReply,
    ) -> None:
        with self._condition:
            if self._entries.get(task_id) is not entry:
                raise DiscordDeliveryCacheError("Discord delivery cache claim was lost")
            lease_id = id(transfer.lease)
            try:
                self._transferred[lease_id] = transfer
                self._entries.pop(task_id)
                self._processing.discard(task_id)
                self._condition.notify_all()
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
            try:
                transfer.entry.generated.cleanup()
            except Exception as exc:
                raise DiscordDeliveryCacheError(
                    "Discord delivery files could not be cleaned up safely"
                ) from exc
        with self._lock:
            current = self._transferred.get(id(lease))
            if current is transfer:
                self._transferred.pop(id(lease))
                if transfer.entry.generated is not None:
                    self._ownership.release(transfer.entry.generated)
