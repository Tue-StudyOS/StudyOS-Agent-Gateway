import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from study_discord_agent.discord_task_persistence import write_document

_SCHEMA_VERSION = 2
_POLICY_CLASSES = {
    "review",
    "security_review",
    "vulnerability_scan",
    "implementation",
}


@dataclass(frozen=True)
class ChannelSessionBinding:
    session_id: str
    policy_class: str | None = None
    policy_fingerprint: str | None = None
    repository_full_name: str | None = None
    commit_sha: str | None = None
    workspace_path: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        metadata = (
            self.policy_class,
            self.policy_fingerprint,
            self.repository_full_name,
            self.commit_sha,
            self.workspace_path,
        )
        if all(value is None for value in metadata):
            return
        if any(value is None for value in metadata):
            raise ValueError("restricted session binding metadata must be complete")
        assert self.policy_class is not None
        assert self.policy_fingerprint is not None
        assert self.repository_full_name is not None
        assert self.commit_sha is not None
        assert self.workspace_path is not None
        if self.policy_class not in _POLICY_CLASSES:
            raise ValueError("session policy class is unsupported")
        if re.fullmatch(r"[0-9a-f]{64}", self.policy_fingerprint) is None:
            raise ValueError("session policy fingerprint is invalid")
        if re.fullmatch(r"Tue-StudyOS/[A-Za-z0-9._-]+", self.repository_full_name) is None:
            raise ValueError("session repository is invalid")
        if re.fullmatch(r"[0-9a-f]{40,64}", self.commit_sha) is None:
            raise ValueError("session commit is invalid")
        if not Path(self.workspace_path).is_absolute():
            raise ValueError("session workspace must be absolute")


class ChannelSessionStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    def get(self, channel_id: int) -> str | None:
        binding = self.get_binding(channel_id)
        return binding.session_id if binding is not None else None

    def get_binding(self, channel_id: int) -> ChannelSessionBinding | None:
        return self._read().get(str(channel_id))

    def set(self, channel_id: int, session_id: str) -> None:
        self.set_binding(channel_id, ChannelSessionBinding(session_id))

    def set_binding(self, channel_id: int, binding: ChannelSessionBinding) -> None:
        if type(channel_id) is not int or channel_id <= 0:
            raise ValueError("channel_id must be a positive integer")
        data = self._read()
        data[str(channel_id)] = binding
        self._write(data)

    def _read(self) -> dict[str, ChannelSessionBinding]:
        if not self._path.exists():
            return {}
        parsed: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Session store must contain a JSON object: {self._path}")
        payload = cast(dict[object, object], parsed)
        if set(payload) == {"version", "sessions"}:
            if payload["version"] != _SCHEMA_VERSION:
                raise RuntimeError("Session store schema is unsupported")
            sessions = payload["sessions"]
            if not isinstance(sessions, dict):
                raise RuntimeError("Session store sessions must be an object")
            return _decode_sessions(cast(dict[object, object], sessions))
        return _decode_legacy(payload)

    def _write(self, data: dict[str, ChannelSessionBinding]) -> None:
        payload = {
            "version": _SCHEMA_VERSION,
            "sessions": {
                key: {name: value for name, value in asdict(binding).items()}
                for key, binding in data.items()
            },
        }
        write_document(
            self._path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )


def _decode_sessions(payload: dict[object, object]) -> dict[str, ChannelSessionBinding]:
    result: dict[str, ChannelSessionBinding] = {}
    expected = {
        "session_id",
        "policy_class",
        "policy_fingerprint",
        "repository_full_name",
        "commit_sha",
        "workspace_path",
    }
    for raw_key, raw_binding in payload.items():
        key = _channel_key(raw_key)
        if not isinstance(raw_binding, dict):
            raise RuntimeError("Session binding schema is unsupported")
        raw_values = cast(dict[object, object], raw_binding)
        if set(raw_values) != expected:
            raise RuntimeError("Session binding schema is unsupported")
        values = cast(dict[str, object], raw_values)
        if not all(value is None or isinstance(value, str) for value in values.values()):
            raise RuntimeError("Session binding values are invalid")
        session_id = values["session_id"]
        if not isinstance(session_id, str):
            raise RuntimeError("Session identifier is invalid")
        result[key] = ChannelSessionBinding(
            session_id=session_id,
            policy_class=_optional_string(values["policy_class"]),
            policy_fingerprint=_optional_string(values["policy_fingerprint"]),
            repository_full_name=_optional_string(values["repository_full_name"]),
            commit_sha=_optional_string(values["commit_sha"]),
            workspace_path=_optional_string(values["workspace_path"]),
        )
    return result


def _decode_legacy(payload: dict[object, object]) -> dict[str, ChannelSessionBinding]:
    result: dict[str, ChannelSessionBinding] = {}
    for raw_key, raw_session in payload.items():
        key = _channel_key(raw_key)
        if not isinstance(raw_session, str) or not raw_session:
            raise RuntimeError("Legacy session binding is invalid")
        result[key] = ChannelSessionBinding(raw_session)
    return result


def _channel_key(value: object) -> str:
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        raise RuntimeError("Session channel identifier is invalid")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def default_session_store_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "gateway" / "discord-channel-sessions.json"
