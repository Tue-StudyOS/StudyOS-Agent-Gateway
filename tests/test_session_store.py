import json
import os
from pathlib import Path

from study_discord_agent.session_store import (
    ChannelSessionBinding,
    ChannelSessionStore,
    default_session_store_path,
)


def test_channel_session_store_round_trips(tmp_path: Path) -> None:
    store = ChannelSessionStore(tmp_path / "sessions.json")

    assert store.get(123) is None

    store.set(123, "session-a")

    assert store.get(123) == "session-a"
    assert ChannelSessionStore(tmp_path / "sessions.json").get(123) == "session-a"


def test_default_session_store_lives_under_codex_home(tmp_path: Path) -> None:
    assert default_session_store_path(str(tmp_path)) == (
        tmp_path / "gateway" / "discord-channel-sessions.json"
    )


def test_legacy_session_is_readable_but_has_no_restricted_binding(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"123": "legacy-thread"}), encoding="utf-8")
    store = ChannelSessionStore(path)

    assert store.get(123) == "legacy-thread"
    assert store.get_binding(123) == ChannelSessionBinding("legacy-thread")


def test_restricted_binding_round_trips_with_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    store = ChannelSessionStore(path)
    binding = ChannelSessionBinding(
        session_id="thread-1",
        policy_class="security_review",
        policy_fingerprint="a" * 64,
        repository_full_name="Tue-StudyOS/example",
        commit_sha="b" * 40,
        workspace_path="/workspaces/Tue-StudyOS/example",
    )

    store.set_binding(123, binding)

    assert ChannelSessionStore(path).get_binding(123) == binding
    assert os.stat(path).st_mode & 0o777 == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["sessions"]["123"]["policy_class"] == "security_review"
