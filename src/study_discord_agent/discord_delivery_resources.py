from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


class DiscordDeliveryLeaseError(RuntimeError):
    """A delivery lease ownership transition was invalid."""


@dataclass(frozen=True)
class PinnedDiscordFile:
    """An immutable snapshot; never reopen ``source_path`` or close ``stream``.

    Each send attempt must create and then close a fresh ``discord.File`` wrapper from
    ``stream`` and ``filename``. The wrapper is non-owning; only the lease closes the
    pinned stream, and cache restoration rewinds it before a later attempt.
    """

    source_path: Path
    filename: str
    size: int
    stream: BinaryIO = field(repr=False, compare=False)


@dataclass
class DiscordDeliveryLease:
    """Owns pinned streams and generated-file cleanup until ``close`` succeeds."""

    files: tuple[PinnedDiscordFile, ...]
    _release: Callable[[], None] = field(repr=False)
    _streams_closed: bool = field(default=False, init=False, repr=False)
    _released: bool = field(default=False, init=False, repr=False)
    _cache_owned: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._streams_closed and self._released

    def close(self) -> None:
        with self._lock:
            if self._cache_owned:
                raise DiscordDeliveryLeaseError(
                    "Discord delivery cache owns this lease"
                )
            self._close_locked()

    def reclaim_for_cache(self, restore: Callable[[], None]) -> None:
        """Rewind and hand this active lease back to its originating cache."""
        with self._lock:
            if self._cache_owned:
                raise DiscordDeliveryLeaseError(
                    "Discord delivery lease is already cache-owned"
                )
            if self._streams_closed or self._released:
                raise DiscordDeliveryLeaseError("Discord delivery lease is no longer active")
            for resource in self.files:
                resource.stream.seek(0)
            restore()
            self._cache_owned = True

    def activate_for_delivery(self, transfer: Callable[[], None]) -> None:
        """Transfer a cache-owned lease back to its delivery caller."""
        with self._lock:
            if not self._cache_owned or self._streams_closed or self._released:
                raise DiscordDeliveryLeaseError(
                    "Discord delivery lease is not ready for retry"
                )
            transfer()
            self._cache_owned = False

    def close_from_cache(self) -> None:
        """Dispose resources while this lease is owned by its cache."""
        with self._lock:
            if not self._cache_owned:
                raise DiscordDeliveryLeaseError(
                    "Discord delivery lease is not owned by the cache"
                )
            self._close_locked()
            self._cache_owned = False

    def _close_locked(self) -> None:
        first_error: BaseException | None = None
        if not self._streams_closed:
            for resource in self.files:
                try:
                    resource.stream.close()
                except BaseException as exc:
                    first_error = first_error or exc
            self._streams_closed = True
        if not self._released:
            try:
                self._release()
            except BaseException as exc:
                first_error = first_error or exc
            else:
                self._released = True
        if first_error is not None:
            raise first_error
