from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordOriginContext:
    channel_id: int
    channel_name: str | None = None
    channel_type: str | None = None
    thread_id: int | None = None
    thread_name: str | None = None
    parent_channel_id: int | None = None
    parent_channel_name: str | None = None
    category_id: int | None = None
    category_name: str | None = None


def render_origin_context(origin: DiscordOriginContext | None) -> str:
    if origin is None:
        return ""

    lines = [
        "Discord origin context:",
        f"- Channel: {_label(origin.channel_name, origin.channel_id, origin.channel_type)}",
    ]
    if origin.thread_id is not None:
        lines.append(f"- Thread: {_label(origin.thread_name, origin.thread_id, None)}")
    if origin.parent_channel_id is not None:
        parent = _label(origin.parent_channel_name, origin.parent_channel_id, None)
        lines.append(
            f"- Parent channel: {parent}"
        )
    if origin.category_id is not None:
        lines.append(f"- Category: {_label(origin.category_name, origin.category_id, None)}")
    return "\n".join(lines)


def _label(name: str | None, item_id: int, item_type: str | None) -> str:
    label = name or "unknown"
    suffix = f", type={item_type}" if item_type else ""
    return f"{label} (id={item_id}{suffix})"
