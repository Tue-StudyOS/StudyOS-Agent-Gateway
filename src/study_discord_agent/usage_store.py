import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from study_discord_agent.codex_command import AgentUsage


@dataclass(frozen=True)
class ChannelUsage:
    channel_id: int
    turns: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    last_session_id: str | None
    updated_at: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChannelUsageStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    def add(self, channel_id: int, usage: AgentUsage, session_id: str | None) -> None:
        if usage.total_tokens <= 0:
            return
        data = self._read()
        key = str(channel_id)
        current = data.get(key, {})
        data[key] = {
            "turns": _int_value(current.get("turns")) + 1,
            "input_tokens": _int_value(current.get("input_tokens")) + usage.input_tokens,
            "cached_input_tokens": _int_value(current.get("cached_input_tokens"))
            + usage.cached_input_tokens,
            "output_tokens": _int_value(current.get("output_tokens")) + usage.output_tokens,
            "reasoning_output_tokens": _int_value(current.get("reasoning_output_tokens"))
            + usage.reasoning_output_tokens,
            "last_session_id": session_id or current.get("last_session_id"),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._write(data)

    def rows(self) -> tuple[ChannelUsage, ...]:
        rows: list[ChannelUsage] = []
        for key, item in self._read().items():
            rows.append(
                ChannelUsage(
                    channel_id=int(key),
                    turns=_int_value(item.get("turns")),
                    input_tokens=_int_value(item.get("input_tokens")),
                    cached_input_tokens=_int_value(item.get("cached_input_tokens")),
                    output_tokens=_int_value(item.get("output_tokens")),
                    reasoning_output_tokens=_int_value(item.get("reasoning_output_tokens")),
                    last_session_id=_str_value(item.get("last_session_id")),
                    updated_at=_str_value(item.get("updated_at")) or "",
                )
            )
        return tuple(sorted(rows, key=lambda row: row.total_tokens, reverse=True))

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.exists():
            return {}
        parsed: Any = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Usage store must contain a JSON object: {self._path}")
        data = cast(dict[object, object], parsed)
        return {
            str(key): cast(dict[str, object], value)
            for key, value in data.items()
            if isinstance(value, dict)
        }

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self._path)


def default_usage_store_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "gateway" / "discord-channel-usage.json"


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _str_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
