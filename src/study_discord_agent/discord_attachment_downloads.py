from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import BinaryIO, Protocol, Self, cast
from urllib.parse import SplitResult, urlsplit

import aiohttp

_CDN_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_DOWNLOAD_CHUNK_BYTES = 64 * 1024


class AttachmentDownloadError(RuntimeError):
    """A Discord attachment could not be streamed through the safe boundary."""


class AttachmentSizeLimitError(AttachmentDownloadError):
    """A streamed attachment crossed its configured byte limit."""


class AttachmentUrlError(AttachmentDownloadError):
    """An attachment URL was outside the Discord CDN boundary."""


class AttachmentContent(Protocol):
    def iter_chunked(self, size: int) -> AsyncIterator[bytes]: ...


class AttachmentResponse(Protocol):
    status: int
    content_length: int | None
    content: AttachmentContent

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class AttachmentSession(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def get(self, url: str, *, allow_redirects: bool) -> AttachmentResponse: ...


AttachmentSessionFactory = Callable[[], AttachmentSession]


class AttachmentDownloader(Protocol):
    async def download(self, url: str, output: BinaryIO, *, max_bytes: int) -> int: ...


@dataclass(frozen=True)
class DiscordCdnAttachmentDownloader:
    """Streams one validated Discord CDN response without following redirects."""

    session_factory: AttachmentSessionFactory = lambda: cast(
        AttachmentSession,
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)),
    )

    async def download(self, url: str, output: BinaryIO, *, max_bytes: int) -> int:
        validated_url = validate_discord_attachment_url(url)
        if type(max_bytes) is not int or max_bytes < 0:
            raise AttachmentDownloadError("Discord attachment size limit is invalid")
        try:
            async with (
                self.session_factory() as session,
                session.get(validated_url, allow_redirects=False) as response,
            ):
                if response.status != 200:
                    raise AttachmentDownloadError(
                        "Discord attachment could not be downloaded"
                    )
                if (
                    response.content_length is not None
                    and response.content_length > max_bytes
                ):
                    raise AttachmentSizeLimitError(
                        "Discord attachment exceeded its size limit"
                    )
                return await _write_bounded_body(response.content, output, max_bytes)
        except AttachmentDownloadError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise AttachmentDownloadError(
                "Discord attachment could not be downloaded"
            ) from exc


def validate_discord_attachment_url(url: object) -> str:
    if not isinstance(url, str):
        raise AttachmentUrlError("Discord attachment URL is invalid")
    try:
        parsed = urlsplit(url)
        _validate_url_parts(parsed)
    except (UnicodeError, ValueError) as exc:
        raise AttachmentUrlError("Discord attachment URL is invalid") from exc
    return url


def _validate_url_parts(parsed: SplitResult) -> None:
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _CDN_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise AttachmentUrlError("Discord attachment URL is invalid")
    parts = parsed.path.split("/")
    if (
        len(parts) != 5
        or parts[0]
        or parts[1] != "attachments"
        or not parts[2].isdigit()
        or not parts[3].isdigit()
        or not parts[4]
    ):
        raise AttachmentUrlError("Discord attachment URL is invalid")


async def _write_bounded_body(
    content: AttachmentContent,
    output: BinaryIO,
    max_bytes: int,
) -> int:
    total = 0
    async for chunk in content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
        remaining = max_bytes - total
        if len(chunk) > remaining:
            if remaining:
                _write_exact(output, chunk[:remaining])
                total += remaining
            raise AttachmentSizeLimitError("Discord attachment exceeded its size limit")
        _write_exact(output, chunk)
        total += len(chunk)
    return total


def _write_exact(output: BinaryIO, chunk: bytes) -> None:
    if output.write(chunk) != len(chunk):
        raise AttachmentDownloadError("Discord attachment could not be written safely")
