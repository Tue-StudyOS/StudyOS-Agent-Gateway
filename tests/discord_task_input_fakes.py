from pathlib import Path
from typing import Any


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        payload: bytes,
        *,
        declared_size: int | None = None,
        error: BaseException | None = None,
        error_after_write: BaseException | None = None,
    ) -> None:
        self.filename = filename
        self.size = len(payload) if declared_size is None else declared_size
        self.payload = payload
        self.error = error
        self.error_after_write = error_after_write
        self.save_calls = 0

    async def save(self, destination: Any) -> int:
        self.save_calls += 1
        if self.error is not None:
            raise self.error
        if hasattr(destination, "write"):
            written = destination.write(self.payload)
        else:
            written = Path(destination).write_bytes(self.payload)
        if self.error_after_write is not None:
            raise self.error_after_write
        return written


class FakeMessage:
    def __init__(self, message_id: int, attachments: list[FakeAttachment]) -> None:
        self.id = message_id
        self.attachments = attachments
