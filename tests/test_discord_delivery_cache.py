from pathlib import Path

import pytest

from study_discord_agent.discord_delivery_cache import (
    DiscordDeliveryCache,
    DiscordDeliveryCacheError,
)
from study_discord_agent.discord_reply_content import PreparedDiscordReply


def _reply(
    generated: Path,
    *artifacts: Path,
) -> PreparedDiscordReply:
    return PreparedDiscordReply(
        message="done",
        files=(*artifacts, generated),
        generated_file=generated,
    )


def test_consume_once_revalidates_and_transfers_generated_file(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    artifact = root / "artifact.txt"
    artifact.write_text("artifact", encoding="utf-8")
    generated = root / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    reply = _reply(generated, artifact)
    cache.put("task-1", reply)
    assert not generated.exists()

    consumed = cache.consume("task-1", (root,), max_bytes=100)

    assert consumed is not None
    assert consumed.delivery_lease is not None
    assert [resource.stream.read() for resource in consumed.delivery_lease.files] == [
        b"artifact",
        b"reply",
    ]
    assert consumed.generated_file is not None
    assert consumed.generated_file.exists()
    assert cache.consume("task-1", (root,), max_bytes=100) is None
    cache.close()
    assert artifact.exists()
    assert consumed.generated_file.exists()
    consumed.delivery_lease.close()
    consumed.delivery_lease.close()
    assert not consumed.generated_file.exists()


def test_missing_artifact_rejects_entry_and_deletes_only_generated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    artifact = root / "artifact.txt"
    artifact.write_text("artifact", encoding="utf-8")
    generated = root / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))
    artifact.unlink()

    assert cache.consume("task-1", (root,), max_bytes=100) is None
    assert not generated.exists()


def test_grown_artifact_rejects_entry_without_deleting_artifact(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    artifact = root / "artifact.txt"
    artifact.write_bytes(b"x" * 101)
    generated = root / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))

    assert cache.consume("task-1", (root,), max_bytes=100) is None
    assert artifact.exists()
    assert not generated.exists()


def test_outside_artifact_rejects_entry_without_deleting_artifact(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    artifact = tmp_path / "outside.txt"
    artifact.write_text("artifact", encoding="utf-8")
    generated = root / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))

    assert cache.consume("task-1", (root,), max_bytes=100) is None
    assert artifact.exists()
    assert not generated.exists()


def test_symlink_artifact_rejects_entry_without_deleting_link_or_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("artifact", encoding="utf-8")
    artifact = root / "artifact-link.txt"
    artifact.symlink_to(target)
    generated = root / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))

    assert cache.consume("task-1", (root,), max_bytes=100) is None
    assert artifact.is_symlink()
    assert target.exists()
    assert not generated.exists()


def test_empty_allowed_roots_rejects_entry_and_deletes_only_generated(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("artifact", encoding="utf-8")
    generated = tmp_path / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated, artifact))

    assert cache.consume("task-1", (), max_bytes=100) is None
    assert artifact.exists()
    assert not generated.exists()


def test_generated_symlink_is_rejected_and_only_link_is_deleted(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "agent-artifact.txt"
    target.write_text("keep", encoding="utf-8")
    generated = root / "reply.md"
    generated.symlink_to(target)
    cache = DiscordDeliveryCache()

    with pytest.raises(DiscordDeliveryCacheError, match="regular file"):
        cache.put("task-1", _reply(generated))

    assert generated.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep"


def test_duplicate_put_is_explicit_and_does_not_take_new_ownership(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.md"
    first.write_text("first", encoding="utf-8")
    second = tmp_path / "second.md"
    second.write_text("second", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(first))
    assert not first.exists()

    with pytest.raises(DiscordDeliveryCacheError, match="already cached"):
        cache.put("task-1", _reply(second))

    assert second.exists()
    cache.discard("task-1")
    assert not first.exists()
    assert second.exists()


def test_closed_put_is_explicit_and_does_not_take_ownership(tmp_path: Path) -> None:
    generated = tmp_path / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.close()

    with pytest.raises(DiscordDeliveryCacheError, match="closed"):
        cache.put("task-1", _reply(generated))

    assert generated.exists()


def test_discard_and_close_are_idempotent_and_delete_only_generated(
    tmp_path: Path,
) -> None:
    first_artifact = tmp_path / "first-artifact.txt"
    first_artifact.write_text("keep", encoding="utf-8")
    first_generated = tmp_path / "first.md"
    first_generated.write_text("delete", encoding="utf-8")
    second_artifact = tmp_path / "second-artifact.txt"
    second_artifact.write_text("keep", encoding="utf-8")
    second_generated = tmp_path / "second.md"
    second_generated.write_text("delete", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("first", _reply(first_generated, first_artifact))
    cache.put("second", _reply(second_generated, second_artifact))

    cache.discard("missing")
    cache.discard("first")
    cache.discard("first")
    cache.close()
    cache.close()

    assert not first_generated.exists()
    assert not second_generated.exists()
    assert first_artifact.read_text(encoding="utf-8") == "keep"
    assert second_artifact.read_text(encoding="utf-8") == "keep"


def test_put_rejects_generated_file_missing_from_files_without_ownership(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("artifact", encoding="utf-8")
    generated = tmp_path / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()
    reply = PreparedDiscordReply(
        message="done",
        files=(artifact,),
        generated_file=generated,
    )

    with pytest.raises(DiscordDeliveryCacheError, match="same reply-file object"):
        cache.put("task-1", reply)

    cache.close()
    assert artifact.exists()
    assert generated.exists()


def test_put_rejects_more_than_ten_reply_files_without_ownership(tmp_path: Path) -> None:
    artifacts = tuple(tmp_path / f"artifact-{index}.txt" for index in range(10))
    for artifact in artifacts:
        artifact.write_text("artifact", encoding="utf-8")
    generated = tmp_path / "reply.md"
    generated.write_text("reply", encoding="utf-8")
    cache = DiscordDeliveryCache()

    with pytest.raises(DiscordDeliveryCacheError, match="at most 10"):
        cache.put("task-1", _reply(generated, *artifacts))

    cache.close()
    assert generated.exists()
    assert all(artifact.exists() for artifact in artifacts)


def test_replaced_generated_path_is_not_deleted_as_owned(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    generated = root / "reply.md"
    generated.write_text("original", encoding="utf-8")
    replacement_target = root / "agent-artifact.txt"
    replacement_target.write_text("keep", encoding="utf-8")
    cache = DiscordDeliveryCache()
    cache.put("task-1", _reply(generated))
    assert not generated.exists()
    generated.symlink_to(replacement_target)

    consumed = cache.consume("task-1", (root,), max_bytes=100)
    assert consumed is not None
    assert consumed.delivery_lease is not None
    assert generated.is_symlink()
    assert replacement_target.read_text(encoding="utf-8") == "keep"
    consumed.delivery_lease.close()
    assert generated.is_symlink()


def test_put_rejects_missing_generated_file(tmp_path: Path) -> None:
    generated = tmp_path / "missing.md"
    cache = DiscordDeliveryCache()

    with pytest.raises(DiscordDeliveryCacheError, match="does not exist"):
        cache.put("task-1", _reply(generated))

    cache.close()
