import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - the gateway is supported only on Linux and macOS
    fcntl = None  # type: ignore[assignment]


class PosixFileLock:
    """A private advisory lock held through a dedicated, stable lock file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def held(self) -> Generator[None]:
        if fcntl is None:  # pragma: no cover - explicit unsupported-platform failure
            raise RuntimeError("POSIX advisory file locking is required")
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:  # pragma: no cover - required on supported platforms
            raise RuntimeError("POSIX no-follow file opening is required")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._path,
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | no_follow,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
