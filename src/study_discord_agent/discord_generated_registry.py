from __future__ import annotations

import threading
from pathlib import Path

from study_discord_agent.discord_delivery_entries import DiscordDeliveryCacheError
from study_discord_agent.discord_file_descriptors import DeliveryFileError
from study_discord_agent.discord_generated_file import (
    FileReservation,
    GeneratedCleanupPending,
    GeneratedFileOwnership,
    PathReservation,
    open_generated_candidate,
)


class GeneratedOwnershipRegistry:
    """Owns generated path/inode reservations and failed quarantine cleanup."""

    def __init__(self) -> None:
        self._paths: set[PathReservation] = set()
        self._files: set[FileReservation] = set()
        self._pending: dict[int, GeneratedFileOwnership] = {}
        self._lock = threading.Lock()

    @property
    def reserved_paths(self) -> frozenset[PathReservation]:
        with self._lock:
            return frozenset(self._paths)

    @property
    def reserved_files(self) -> frozenset[FileReservation]:
        with self._lock:
            return frozenset(self._files)

    def acquire(
        self,
        generated_file: Path,
    ) -> GeneratedFileOwnership:
        try:
            candidate = open_generated_candidate(generated_file)
        except DeliveryFileError as exc:
            raise DiscordDeliveryCacheError(exc.public_message) from exc
        except Exception as exc:
            raise DiscordDeliveryCacheError(
                "Generated Discord reply ownership could not be established safely"
            ) from exc
        try:
            if self._is_reserved(candidate.path_reservation, candidate.file_reservation):
                raise DiscordDeliveryCacheError(
                    "Generated Discord reply file is already owned"
                )
            ownership = candidate.quarantine()
        except DiscordDeliveryCacheError:
            raise
        except GeneratedCleanupPending as pending:
            self._register_pending(pending.ownership)
            if not isinstance(pending.original, Exception):
                raise pending.original from pending
            raise DiscordDeliveryCacheError(
                "Generated Discord reply cleanup is pending"
            ) from pending.original
        except DeliveryFileError as exc:
            raise DiscordDeliveryCacheError(exc.public_message) from exc
        except Exception as exc:
            raise DiscordDeliveryCacheError(
                "Generated Discord reply ownership could not be established safely"
            ) from exc
        finally:
            candidate.close()
        self.reserve(ownership)
        return ownership

    def reserve(self, generated: GeneratedFileOwnership) -> None:
        with self._lock:
            self._paths.add(generated.path_reservation)
            self._files.add(generated.file_reservation)

    def release(self, generated: GeneratedFileOwnership) -> None:
        with self._lock:
            self._paths.discard(generated.path_reservation)
            self._files.discard(generated.file_reservation)

    def drain_pending(self) -> BaseException | None:
        with self._lock:
            pending = tuple(self._pending.values())
        first_error: BaseException | None = None
        for generated in pending:
            try:
                generated.cleanup()
            except BaseException as exc:
                first_error = first_error or exc
            else:
                with self._lock:
                    if self._pending.get(id(generated)) is generated:
                        self._pending.pop(id(generated))
                        self._paths.discard(generated.path_reservation)
                        self._files.discard(generated.file_reservation)
        return first_error

    def _is_reserved(
        self,
        path: PathReservation,
        file: FileReservation,
    ) -> bool:
        with self._lock:
            return path in self._paths or file in self._files

    def _register_pending(self, generated: GeneratedFileOwnership) -> None:
        with self._lock:
            self._paths.add(generated.path_reservation)
            self._files.add(generated.file_reservation)
            self._pending[id(generated)] = generated
