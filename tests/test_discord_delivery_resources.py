import io
from pathlib import Path

import pytest

from study_discord_agent.discord_delivery_resources import (
    DiscordDeliveryLease,
    PinnedDiscordFile,
)


class FlakyCloseStream(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("stream close unavailable")
        super().close()


def test_lease_retries_a_stream_close_that_previously_failed(tmp_path: Path) -> None:
    stream = FlakyCloseStream(b"reply")
    release_calls = 0

    def release() -> None:
        nonlocal release_calls
        release_calls += 1

    lease = DiscordDeliveryLease(
        files=(PinnedDiscordFile(tmp_path / "reply.txt", "reply.txt", 5, stream),),
        _release=release,
    )

    with pytest.raises(OSError, match="stream close unavailable"):
        lease.close()

    assert not lease.closed
    assert not stream.closed
    assert stream.close_calls == 1
    assert release_calls == 1

    lease.close()

    assert lease.closed
    assert stream.closed
    assert stream.close_calls == 2
    assert release_calls == 1
