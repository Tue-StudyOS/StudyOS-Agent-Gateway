import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

LOCAL_FILE_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:/workspace|/workspaces|/tmp)[^)]+)\)")
LOCAL_FILE_PATH_RE = re.compile(
    r"(?<![\w/])((?:/workspace|/workspaces|/tmp)/[^\s)>,]+"
    r"\.(?:pdf|png|jpe?g|webp|gif|svg|txt|md|csv|json|zip|docx|pptx|xlsx|tex)"
    r"(?!:\d))"
)
LOCAL_SOURCE_LOCATION_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")


@dataclass(frozen=True)
class ParsedAgentReply:
    message: str
    files: tuple[Path, ...] = ()


def parse_agent_reply(text: str) -> ParsedAgentReply:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return parse_text_artifacts(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return ParsedAgentReply(message=text)
    if not isinstance(parsed, dict):
        return ParsedAgentReply(message=text)

    data = cast(dict[str, object], parsed)
    if "message" not in data or "files" not in data:
        return ParsedAgentReply(message=text)
    message = data["message"]
    if not isinstance(message, str):
        raise RuntimeError("Agent artifact response field 'message' must be a string")
    return ParsedAgentReply(message=message, files=parse_artifact_files(data["files"]))


def parse_text_artifacts(text: str) -> ParsedAgentReply:
    files: list[Path] = []

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        path = match.group(2).strip()
        if LOCAL_SOURCE_LOCATION_SUFFIX_RE.search(path):
            return match.group(0)
        files.append(Path(path))
        return f"{label} (attached)"

    message = LOCAL_FILE_LINK_RE.sub(replace_link, text)
    for match in LOCAL_FILE_PATH_RE.finditer(message):
        files.append(Path(match.group(1)))

    return ParsedAgentReply(message=message, files=_deduplicate_paths(files))


def parse_artifact_files(value: object) -> tuple[Path, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeError("Agent artifact response field 'files' must be a list")

    raw_files = cast(list[object], value)
    files: list[Path] = []
    for item in raw_files:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError("Agent artifact file entries must be non-empty strings")
        files.append(Path(item))
    return _deduplicate_paths(files)


def _deduplicate_paths(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)
