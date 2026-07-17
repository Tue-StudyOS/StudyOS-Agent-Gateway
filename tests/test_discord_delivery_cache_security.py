import os
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest

from study_discord_agent.discord_delivery_cache import (
    DiscordDeliveryCache,
    DiscordDeliveryCacheError,
)
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


def test_generated_membership_requires_exact_object_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "reply.md"
    artifact.write_text("artifact", encoding="utf-8")
    equal_but_distinct = Path(str(artifact))
    assert equal_but_distinct == artifact
    assert equal_but_distinct is not artifact
    reply = PreparedDiscordReply(
        message="done",
        files=(artifact,),
        generated_file=equal_but_distinct,
    )
    cache = DiscordDeliveryCache()

    with pytest.raises(DiscordDeliveryCacheError, match="same reply-file object"):
        cache.put("task-1", reply)

    assert artifact.read_text(encoding="utf-8") == "artifact"


def test_generated_identity_is_exclusive_while_cached_and_in_flight(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    hardlink = tmp_path / "same-file.md"
    os.link(generated, hardlink)
    cache = DiscordDeliveryCache()
    cache.put("first", _reply(generated))

    with pytest.raises(DiscordDeliveryCacheError, match="already owned"):
        cache.put("second", _reply(hardlink))

    consumed = cache.consume("first", (tmp_path,), max_bytes=100)
    assert consumed is not None
    assert consumed.delivery_lease is not None

    with pytest.raises(DiscordDeliveryCacheError, match="already owned"):
        cache.put("second", _reply(hardlink))

    cache.close()
    assert consumed.generated_file is not None
    assert consumed.generated_file.exists()
    consumed.delivery_lease.close()
    cache = DiscordDeliveryCache()
    cache.put("second", _reply(hardlink))
    cache.discard("second")


def test_consume_delivers_immutable_snapshot_when_source_path_is_swapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(b"safe")
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))
    original_temporary_file = tempfile.TemporaryFile
    swapped = False

    def swap_before_snapshot(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal swapped
        if not swapped:
            swapped = True
            moved = tmp_path / "original-artifact.txt"
            artifact.rename(moved)
            artifact.write_bytes(b"x" * 101)
        return original_temporary_file(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryFile", swap_before_snapshot)

    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)

    assert swapped
    assert consumed is not None
    assert consumed.delivery_lease is not None
    assert consumed.delivery_lease.files[0].stream.read() == b"safe"
    assert artifact.stat().st_size == 101
    consumed.delivery_lease.close()


def test_validation_abort_retains_entry_and_generated_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(b"artifact")
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))
    original_temporary_file = tempfile.TemporaryFile
    abort = CacheAbort("validation stopped")

    def abort_temporary_file(*_args: object, **_kwargs: object) -> NoReturn:
        raise abort

    with monkeypatch.context() as scoped:
        scoped.setattr(
            tempfile,
            "TemporaryFile",
            abort_temporary_file,
        )
        with pytest.raises(CacheAbort) as raised:
            cache.consume("task-1", (tmp_path,), max_bytes=100)
        assert raised.value is abort

    consumed = cache.consume("task-1", (tmp_path,), max_bytes=100)
    assert consumed is not None
    assert consumed.delivery_lease is not None
    consumed.delivery_lease.close()
    assert original_temporary_file is tempfile.TemporaryFile


def test_failed_discard_retains_cleanup_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    original_unlink = os.unlink
    abort = CacheAbort("cleanup stopped")

    def abort_unlink(*_args: object, **_kwargs: object) -> NoReturn:
        raise abort

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "unlink", abort_unlink)
        with pytest.raises(CacheAbort) as raised:
            cache.discard("task-1")
        assert raised.value is abort

    cache.discard("task-1")
    assert _quarantines(tmp_path) == ()
    assert original_unlink is os.unlink


def test_close_drains_other_entries_and_retries_failed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.md"
    first.write_bytes(b"first")
    second = tmp_path / "second.md"
    second.write_bytes(b"second")
    cache = DiscordDeliveryCache()
    cache.put("first", _reply(first))
    cache.put("second", _reply(second))
    original_unlink = os.unlink
    abort = CacheAbort("first cleanup stopped")
    calls = 0

    def abort_once(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise abort
        original_unlink(*args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "unlink", abort_once)
        with pytest.raises(CacheAbort) as raised:
            cache.close()
        assert raised.value is abort

    assert len(_quarantines(tmp_path)) == 1
    cache.close()
    assert _quarantines(tmp_path) == ()


def test_original_path_replacement_survives_descriptor_anchored_cleanup(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"owned")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    assert not generated.exists()
    generated.write_bytes(b"unrelated replacement")

    cache.discard("task-1")

    assert generated.read_bytes() == b"unrelated replacement"
    assert _quarantines(tmp_path) == ()
