import os
from pathlib import Path
from typing import NoReturn

import pytest

from study_discord_agent import discord_delivery_entries
from study_discord_agent.discord_delivery_cache import (
    DiscordDeliveryCache,
    DiscordDeliveryCacheError,
)
from study_discord_agent.discord_delivery_resources import DiscordDeliveryLeaseError
from study_discord_agent.discord_reply_content import PreparedDiscordReply


class CacheAbort(BaseException):
    pass


def _reply(generated: Path, *artifacts: Path) -> PreparedDiscordReply:
    return PreparedDiscordReply(
        message="done",
        files=(*artifacts, generated),
        generated_file=generated,
    )


def _quarantines(parent: Path) -> tuple[Path, ...]:
    return tuple(path for path in parent.iterdir() if path.name.startswith(".studyos-delivery-"))


def test_definitive_failure_restores_exact_pinned_reply_without_reopening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(b"immutable artifact")
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"immutable reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None
    assert lease.files[0].stream.read(9) == b"immutable"

    artifact.write_bytes(b"replacement")
    cache.restore("task-1", consumed)

    with pytest.raises(DiscordDeliveryLeaseError, match="cache owns"):
        lease.close()

    def fail_if_reopened(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("restored reply reopened its source")

    monkeypatch.setattr(
        discord_delivery_entries,
        "snapshot_allowed_file",
        fail_if_reopened,
    )
    retried = cache.consume("task-1", (tmp_path,), max_bytes=100)

    assert retried is consumed
    assert lease.files[0].stream.read() == b"immutable artifact"
    assert lease.files[1].stream.read() == b"immutable reply"
    lease.close()
    assert _quarantines(tmp_path) == ()


def test_restore_rejects_wrong_cache_task_or_reply_without_consuming_lease(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    other_cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None
    copied_reply = PreparedDiscordReply(
        message=consumed.message,
        files=consumed.files,
        generated_file=consumed.generated_file,
        delivery_lease=lease,
    )

    with pytest.raises(DiscordDeliveryCacheError, match="original task"):
        cache.restore("wrong-task", consumed)
    with pytest.raises(DiscordDeliveryCacheError, match="exact in-flight reply"):
        cache.restore("task-1", copied_reply)
    with pytest.raises(DiscordDeliveryCacheError, match="this cache"):
        other_cache.restore("task-1", consumed)

    cache.restore("task-1", consumed)
    with pytest.raises(DiscordDeliveryLeaseError, match="already cache-owned"):
        cache.restore("task-1", consumed)
    retried = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert retried is consumed
    lease.close()


def test_restored_reply_can_be_rearmed_for_multiple_definitive_failures(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None

    for _ in range(2):
        assert lease.files[0].stream.read() == b"reply"
        cache.restore("task-1", consumed)
        assert cache.consume("task-1", (tmp_path,), max_bytes=100) is consumed

    lease.close()


def test_in_flight_task_id_cannot_be_replaced_before_restore(tmp_path: Path) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None

    with pytest.raises(DiscordDeliveryCacheError, match="in flight"):
        cache.put("task-1", PreparedDiscordReply(message="replacement", files=()))

    cache.restore("task-1", consumed)
    assert cache.consume("task-1", (tmp_path,), max_bytes=100) is consumed
    lease.close()


def test_cache_close_disposes_a_restored_lease(tmp_path: Path) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None
    cache.restore("task-1", consumed)

    cache.close()

    assert lease.closed
    assert _quarantines(tmp_path) == ()
    lease.close()


def test_terminal_lease_cleanup_failure_retains_reservation_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    hardlink = tmp_path / "same-file.md"
    os.link(generated, hardlink)
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    lease = consumed.delivery_lease
    assert lease is not None
    abort = CacheAbort("terminal cleanup stopped")

    def abort_unlink(*_args: object, **_kwargs: object) -> NoReturn:
        raise abort

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "unlink", abort_unlink)
        with pytest.raises(CacheAbort) as raised:
            lease.close()
        assert raised.value is abort

    with pytest.raises(DiscordDeliveryCacheError, match="already owned"):
        cache.put("task-2", _reply(hardlink))

    lease.close()
    cache.put("task-2", _reply(hardlink))
    cache.discard("task-2")
