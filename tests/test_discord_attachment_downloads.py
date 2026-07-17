import asyncio
import io
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

import pytest

from study_discord_agent.discord_attachment_downloads import (
    AttachmentContent,
    AttachmentDownloadError,
    AttachmentResponse,
    AttachmentSession,
    DiscordCdnAttachmentDownloader,
)
from study_discord_agent.discord_task_inputs import MAX_DISCORD_INPUT_ATTACHMENT_BYTES

VALID_URL = "https://cdn.discordapp.com/attachments/1/2/input.txt?ex=signed"


class FakeContent:
    def __init__(
        self,
        chunk: bytes,
        count: int,
        *,
        error_after: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.chunk = chunk
        self.count = count
        self.error_after = error_after
        self.error = error
        self.yielded = 0

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        assert size == 64 * 1024
        for _ in range(self.count):
            if self.error_after == self.yielded and self.error is not None:
                raise self.error
            self.yielded += 1
            yield self.chunk


class FakeResponse:
    def __init__(
        self,
        content: FakeContent,
        *,
        status: int = 200,
        content_length: int | None = None,
    ) -> None:
        self.content: AttachmentContent = content
        self.status = status
        self.content_length = content_length

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.get_calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def get(self, url: str, *, allow_redirects: bool) -> AttachmentResponse:
        assert url == VALID_URL
        assert not allow_redirects
        self.get_calls += 1
        return self.response


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.calls = 0

    def __call__(self) -> AttachmentSession:
        self.calls += 1
        return self.session


@pytest.mark.asyncio
async def test_streams_valid_discord_cdn_attachment_in_bounded_chunks() -> None:
    content = FakeContent(b"abc", 3)
    factory = FakeSessionFactory(FakeSession(FakeResponse(content, content_length=9)))
    downloader = DiscordCdnAttachmentDownloader(session_factory=factory)
    output = io.BytesIO()

    written = await downloader.download(VALID_URL, output, max_bytes=100)

    assert written == 9
    assert output.getvalue() == b"abcabcabc"
    assert content.yielded == 3


@pytest.mark.asyncio
async def test_stops_stream_when_lying_body_crosses_hard_limit() -> None:
    chunk = b"x" * (64 * 1024)
    content = FakeContent(chunk, 200)
    factory = FakeSessionFactory(FakeSession(FakeResponse(content)))
    downloader = DiscordCdnAttachmentDownloader(session_factory=factory)
    output = io.BytesIO()

    with pytest.raises(AttachmentDownloadError, match="size limit"):
        await downloader.download(
            VALID_URL,
            output,
            max_bytes=MAX_DISCORD_INPUT_ATTACHMENT_BYTES,
        )

    assert len(output.getvalue()) == MAX_DISCORD_INPUT_ATTACHMENT_BYTES
    assert content.yielded < content.count
    assert content.yielded * len(chunk) <= MAX_DISCORD_INPUT_ATTACHMENT_BYTES + len(chunk)


@pytest.mark.asyncio
async def test_rejects_oversize_content_length_before_reading_body() -> None:
    content = FakeContent(b"never", 1)
    response = FakeResponse(
        content,
        content_length=MAX_DISCORD_INPUT_ATTACHMENT_BYTES + 1,
    )
    downloader = DiscordCdnAttachmentDownloader(
        session_factory=FakeSessionFactory(FakeSession(response))
    )

    with pytest.raises(AttachmentDownloadError, match="size limit"):
        await downloader.download(VALID_URL, io.BytesIO(), max_bytes=8_000_000)

    assert content.yielded == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.discordapp.com/attachments/1/2/input.txt",
        "https://example.com/attachments/1/2/input.txt",
        "https://cdn.discordapp.com/not-attachments/1/2/input.txt",
        "https://cdn.discordapp.com/attachments/not-an-id/2/input.txt",
        "https://user@cdn.discordapp.com/attachments/1/2/input.txt",
    ],
)
async def test_rejects_non_cdn_urls_before_opening_session(url: str) -> None:
    factory = FakeSessionFactory(FakeSession(FakeResponse(FakeContent(b"", 0))))
    downloader = DiscordCdnAttachmentDownloader(session_factory=factory)

    with pytest.raises(AttachmentDownloadError, match="URL is invalid"):
        await downloader.download(url, io.BytesIO(), max_bytes=100)

    assert factory.calls == 0


@pytest.mark.asyncio
async def test_wraps_partial_network_failure_with_static_error() -> None:
    content = FakeContent(
        b"partial",
        2,
        error_after=1,
        error=OSError("private-network-detail"),
    )
    downloader = DiscordCdnAttachmentDownloader(
        session_factory=FakeSessionFactory(FakeSession(FakeResponse(content)))
    )
    output = io.BytesIO()

    with pytest.raises(AttachmentDownloadError, match="could not be downloaded") as raised:
        await downloader.download(VALID_URL, output, max_bytes=100)

    assert "private-network-detail" not in str(raised.value)
    assert output.getvalue() == b"partial"


@pytest.mark.asyncio
async def test_preserves_stream_cancellation() -> None:
    cancelled = asyncio.CancelledError()
    content = FakeContent(b"partial", 2, error_after=1, error=cancelled)
    downloader = DiscordCdnAttachmentDownloader(
        session_factory=FakeSessionFactory(FakeSession(FakeResponse(content)))
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await downloader.download(VALID_URL, io.BytesIO(), max_bytes=100)

    assert raised.value is cancelled
