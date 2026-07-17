import re
from dataclasses import dataclass
from pathlib import Path

from study_discord_agent.agent_errors import AgentWorkspaceOrAttachmentError

MAX_INLINE_REPLY_CHARS = 900
MAX_INLINE_REPLY_LINES = 12
MAX_INLINE_SUMMARY_CHARS = 320
MAX_DISCORD_ATTACHMENTS = 10
MARKDOWN_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+\S")
DELIVERY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class PreparedDiscordReply:
    message: str
    files: tuple[Path, ...]
    generated_file: Path | None = None


def prepare_discord_reply(
    message: str,
    files: tuple[Path, ...],
    artifact_root: Path,
    delivery_key: str,
) -> PreparedDiscordReply:
    _validate_delivery_key(delivery_key)
    if not _needs_attachment(message):
        return PreparedDiscordReply(message=message, files=files)
    if len(files) >= MAX_DISCORD_ATTACHMENTS:
        raise AgentWorkspaceOrAttachmentError("Discord reply already has the attachment limit")

    try:
        output_dir = artifact_root / "discord-replies"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"reply-{delivery_key}.md"
        output_path.write_text(message.rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        raise AgentWorkspaceOrAttachmentError(
            "Discord reply attachment could not be prepared",
        ) from exc
    return PreparedDiscordReply(
        message=_inline_summary(message),
        files=files + (output_path,),
        generated_file=output_path,
    )


def _validate_delivery_key(delivery_key: str) -> None:
    if DELIVERY_KEY_RE.fullmatch(delivery_key):
        return
    raise AgentWorkspaceOrAttachmentError("Discord reply delivery key is invalid")


def _needs_attachment(message: str) -> bool:
    return bool(
        len(message) > MAX_INLINE_REPLY_CHARS
        or len(message.splitlines()) > MAX_INLINE_REPLY_LINES
        or "```" in message
        or "~~~" in message
        or MARKDOWN_HEADING_RE.search(message)
    )


def _inline_summary(message: str) -> str:
    in_code_block = False
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if line.startswith(("```", "~~~")):
            in_code_block = not in_code_block
            continue
        if in_code_block or not line or line.startswith(("#", "- ", "* ", ">")):
            continue
        cleaned = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", line)
        cleaned = cleaned.replace("**", "").replace("__", "").strip()
        if cleaned:
            if len(cleaned) > MAX_INLINE_SUMMARY_CHARS:
                cleaned = f"{cleaned[: MAX_INLINE_SUMMARY_CHARS - 1].rstrip()}…"
            return f"{cleaned}\n\nFull write-up's attached — way nicer than a Discord wall."
    return "Dropped the full code/write-up into the attachment — easier to read there."
