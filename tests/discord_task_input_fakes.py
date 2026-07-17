from typing import Any, BinaryIO

from study_discord_agent.discord_attachment_downloads import (
    AttachmentDownloadError,
    AttachmentSizeLimitError,
)

_ATTACHMENTS_BY_URL: dict[str, "FakeAttachment"] = {}
_next_attachment_id = 1


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        payload: bytes,
        *,
        declared_size: int | None = None,
        error: BaseException | None = None,
        error_after_write: BaseException | None = None,
        url: str | None = None,
    ) -> None:
        global _next_attachment_id
        self.filename = filename
        self.size = len(payload) if declared_size is None else declared_size
        self.payload = payload
        self.error = error
        self.error_after_write = error_after_write
        self.save_calls = 0
        self.written_bytes = 0
        self.url = url or (
            "https://cdn.discordapp.com/attachments/1/"
            f"{_next_attachment_id}/input.bin"
        )
        _next_attachment_id += 1
        _ATTACHMENTS_BY_URL[self.url] = self

    async def save(self, destination: Any) -> int:
        raise AssertionError("Attachment.save must not be used by bounded staging")


class FakeMessage:
    def __init__(self, message_id: int, attachments: list[FakeAttachment]) -> None:
        self.id = message_id
        self.attachments = attachments


class FakeAttachmentDownloader:
    async def download(self, url: str, output: BinaryIO, *, max_bytes: int) -> int:
        attachment = _ATTACHMENTS_BY_URL.get(url)
        if attachment is None:
            raise AttachmentDownloadError("Discord attachment could not be downloaded")
        attachment.save_calls += 1
        if attachment.error is not None:
            raise attachment.error
        payload = attachment.payload
        written = output.write(payload[:max_bytes])
        attachment.written_bytes += written
        if len(payload) > max_bytes:
            raise AttachmentSizeLimitError("Discord attachment exceeded its size limit")
        if attachment.error_after_write is not None:
            raise attachment.error_after_write
        return written
