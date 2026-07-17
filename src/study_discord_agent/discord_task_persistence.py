import os
import tempfile
from pathlib import Path


class TaskStoreDurabilityError(RuntimeError):
    """The replacement reached disk but its directory durability check failed."""


def write_document(path: Path, document: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        output = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with output:
            output.write(document)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        confirm_document_durability(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def confirm_document_durability(path: Path) -> None:
    try:
        _fsync_directory(path.parent)
    except OSError as error:
        raise TaskStoreDurabilityError(
            "Discord task store replacement needs directory sync"
        ) from error


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
