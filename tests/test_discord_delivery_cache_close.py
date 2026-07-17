import os
import threading
from pathlib import Path
from typing import NoReturn

import pytest

from study_discord_agent import discord_delivery_entries, discord_generated_file
from study_discord_agent.discord_delivery_cache import (
    DiscordDeliveryCache,
    DiscordDeliveryCacheError,
)
from study_discord_agent.discord_reply_content import PreparedDiscordReply


class CacheAbort(BaseException):
    pass


def _fail_unlink(*_args: object, **_kwargs: object) -> NoReturn:
    raise OSError("/private/generated-path")


def _reply(generated: Path) -> PreparedDiscordReply:
    return PreparedDiscordReply(
        message="done",
        files=(generated,),
        generated_file=generated,
    )


def test_close_waits_for_claim_abort_then_drains_retained_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    hardlink = tmp_path / "same-file.md"
    os.link(generated, hardlink)
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    entered_validation = threading.Event()
    release_validation = threading.Event()
    close_done = threading.Event()
    abort = CacheAbort("validation stopped")
    consume_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    def blocked_absolute_path(_path: Path) -> Path:
        entered_validation.set()
        assert release_validation.wait(timeout=2)
        raise abort

    def consume_worker() -> None:
        try:
            cache.consume("task-1", (tmp_path,), max_bytes=100)
        except BaseException as exc:
            consume_errors.append(exc)

    def close_worker() -> None:
        try:
            cache.close()
        except BaseException as exc:
            close_errors.append(exc)
        finally:
            close_done.set()

    monkeypatch.setattr(discord_delivery_entries, "absolute_path", blocked_absolute_path)
    consume_thread = threading.Thread(target=consume_worker)
    consume_thread.start()
    assert entered_validation.wait(timeout=2)
    close_thread = threading.Thread(target=close_worker)
    close_thread.start()
    assert not close_done.wait(timeout=0.05)

    release_validation.set()
    consume_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert consume_errors == [abort]
    assert close_errors == []
    assert close_done.is_set()
    assert cache._entries == {}  # pyright: ignore[reportPrivateUsage]
    assert cache._processing == set()  # pyright: ignore[reportPrivateUsage]
    assert cache._ownership.reserved_paths == set()  # pyright: ignore[reportPrivateUsage]
    assert cache._ownership.reserved_files == set()  # pyright: ignore[reportPrivateUsage]
    assert tuple(path for path in tmp_path.iterdir() if path.name.startswith(".studyos-")) == ()
    assert hardlink.read_bytes() == b"reply"


def test_discard_wraps_os_failure_without_exposing_path_and_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "private-reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))

    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "unlink",
            _fail_unlink,
        )
        with pytest.raises(
            DiscordDeliveryCacheError,
            match="could not be cleaned up safely",
        ) as raised:
            cache.discard("task-1")

    assert "/private/generated-path" not in str(raised.value)
    cache.discard("task-1")


def test_put_wraps_quarantine_os_failure_without_exposing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "private-reply.md"
    generated.write_bytes(b"reply")
    cache = DiscordDeliveryCache()

    def fail_mode(_descriptor: int, _mode: int) -> None:
        raise OSError("/private/quarantine-path")

    monkeypatch.setattr(discord_generated_file.os, "fchmod", fail_mode)

    with pytest.raises(
        DiscordDeliveryCacheError,
        match="ownership could not be established safely",
    ) as raised:
        cache.put("task-1", _reply(generated))

    assert "/private/quarantine-path" not in str(raised.value)
    assert generated.read_bytes() == b"reply"


def test_failed_quarantine_restore_is_retained_for_cache_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"owned")
    cache = DiscordDeliveryCache()
    original_open_regular = discord_generated_file.open_regular
    calls = 0

    def fail_post_move_validation(
        name: str,
        *,
        dir_fd: int,
    ) -> tuple[int, os.stat_result]:
        nonlocal calls
        calls += 1
        if calls == 2:
            generated.write_bytes(b"unrelated replacement")
            raise OSError("/private/post-move-validation")
        return original_open_regular(name, dir_fd=dir_fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            discord_generated_file,
            "open_regular",
            fail_post_move_validation,
        )
        with pytest.raises(
            DiscordDeliveryCacheError,
            match="cleanup is pending",
        ) as raised:
            cache.put("task-1", _reply(generated))

    assert "/private/post-move-validation" not in str(raised.value)
    assert generated.read_bytes() == b"unrelated replacement"
    assert len(tuple(tmp_path.glob(".studyos-delivery-*"))) == 1

    cache.close()

    assert generated.read_bytes() == b"unrelated replacement"
    assert tuple(tmp_path.glob(".studyos-delivery-*")) == ()
    assert cache._ownership.reserved_paths == set()  # pyright: ignore[reportPrivateUsage]
    assert cache._ownership.reserved_files == set()  # pyright: ignore[reportPrivateUsage]


def test_generated_parent_owned_by_another_os_identity_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    actual_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(discord_generated_file.os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(DiscordDeliveryCacheError, match="parent is unsafe"):
        DiscordDeliveryCache().put("task-1", _reply(generated))

    assert generated.read_bytes() == b"reply"
    assert tuple(tmp_path.glob(".studyos-delivery-*")) == ()


def test_group_writable_generated_parent_is_rejected(tmp_path: Path) -> None:
    generated = tmp_path / "reply.md"
    generated.write_bytes(b"reply")
    tmp_path.chmod(0o770)

    with pytest.raises(DiscordDeliveryCacheError, match="parent is unsafe"):
        DiscordDeliveryCache().put("task-1", _reply(generated))

    assert generated.read_bytes() == b"reply"


def test_symlink_generated_parent_is_not_followed(tmp_path: Path) -> None:
    owned_parent = tmp_path / "owned-parent"
    owned_parent.mkdir(mode=0o700)
    generated = owned_parent / "reply.md"
    generated.write_bytes(b"reply")
    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(owned_parent, target_is_directory=True)

    with pytest.raises(DiscordDeliveryCacheError, match="parent is unsafe"):
        DiscordDeliveryCache().put("task-1", _reply(parent_link / "reply.md"))

    assert parent_link.is_symlink()
    assert generated.read_bytes() == b"reply"
