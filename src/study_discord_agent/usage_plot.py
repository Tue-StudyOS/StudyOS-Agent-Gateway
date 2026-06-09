import argparse
import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

from study_discord_agent.usage_store import (
    ChannelUsage,
    ChannelUsageStore,
    default_usage_store_path,
)

DEFAULT_OUTPUT_PATH = Path("/tmp/studyos-artifacts/discord-channel-usage.png")


def render_usage_dot(path: Path, limit: int, labels: dict[int, str] | None = None) -> str:
    rows = ChannelUsageStore(path).rows()[:limit]
    body = [
        "digraph usage {",
        '  graph [bgcolor="#f8fafc", pad="0.35"];',
        '  node [shape=plain, fontname="Helvetica"];',
        "  usage [label=<",
        '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="8">',
        '<TR><TD ALIGN="LEFT" COLSPAN="5">'
        '<FONT POINT-SIZE="22"><B>StudyOS Codex Usage by Discord Channel</B></FONT>'
        "</TD></TR>",
    ]
    if not rows:
        body.extend(
            [
                '<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="16">'
                "No usage recorded yet.</FONT></TD></TR>",
                "</TABLE>>];",
                "}",
            ]
        )
        return "\n".join(body)

    body.append(_header_row())
    max_total = max(row.total_tokens for row in rows) or 1
    for row in rows:
        body.append(_usage_row(row, max_total, labels or {}))
    body.extend(["</TABLE>>];", "}"])
    return "\n".join(body)


def write_usage_png(path: Path, output: Path, limit: int, labels_path: Path | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    dot = render_usage_dot(path, limit, _load_labels(labels_path))
    try:
        with output.open("wb") as output_file:
            subprocess.run(
                ["dot", "-Tpng"],
                input=dot.encode("utf-8"),
                stdout=output_file,
                stderr=subprocess.PIPE,
                check=True,
            )
    except FileNotFoundError as exc:
        raise RuntimeError("studyos-usage-plot requires Graphviz `dot`") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Graphviz failed to render usage plot: {_stderr_text(exc)}") from exc
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Render StudyOS Codex token usage as PNG.")
    parser.add_argument(
        "--path",
        type=Path,
        default=default_usage_store_path(os.environ.get("CODEX_HOME")),
        help="Usage JSON path. Defaults to $CODEX_HOME/gateway/discord-channel-usage.json.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--labels-json", type=Path, default=None)
    args = parser.parse_args()
    print(write_usage_png(args.path, args.output, args.limit, args.labels_json))


def _header_row() -> str:
    return (
        '<TR><TD ALIGN="LEFT"><B>Channel</B></TD>'
        '<TD ALIGN="RIGHT"><B>Total</B></TD>'
        '<TD ALIGN="RIGHT"><B>Turns</B></TD>'
        '<TD ALIGN="RIGHT"><B>In / Out</B></TD>'
        '<TD ALIGN="LEFT"><B>Relative usage</B></TD></TR>'
    )


def _usage_row(row: ChannelUsage, max_total: int, labels: dict[int, str]) -> str:
    channel = html.escape(labels.get(row.channel_id) or str(row.channel_id))
    in_out = html.escape(f"{row.input_tokens:,} / {row.output_tokens:,}")
    return (
        f'<TR><TD ALIGN="LEFT">{channel}</TD>'
        f'<TD ALIGN="RIGHT">{row.total_tokens:,}</TD>'
        f'<TD ALIGN="RIGHT">{row.turns}</TD>'
        f'<TD ALIGN="RIGHT">{in_out}</TD>'
        f'<TD ALIGN="LEFT">{_bar_table(row, max_total)}</TD></TR>'
    )


def _bar_table(row: ChannelUsage, max_total: int) -> str:
    max_width = 320
    used_width = max(2, round(max_width * row.total_tokens / max_total))
    input_width = round(used_width * row.input_tokens / row.total_tokens)
    output_width = used_width - input_width
    remaining_width = max_width - used_width
    cells = [
        _bar_cell("#2563eb", input_width),
        _bar_cell("#16a34a", output_width),
        _bar_cell("#e2e8f0", remaining_width),
    ]
    return (
        '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0"><TR>'
        + "".join(cell for cell in cells if cell)
        + "</TR></TABLE>"
    )


def _bar_cell(color: str, width: int) -> str:
    if width <= 0:
        return ""
    return f'<TD FIXEDSIZE="TRUE" WIDTH="{width}" HEIGHT="20" BGCOLOR="{color}"></TD>'


def _stderr_text(exc: subprocess.CalledProcessError) -> str:
    value = exc.stderr
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:500]
    if isinstance(value, str):
        return value[:500]
    return "unknown error"


def _load_labels(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Channel label map must be a JSON object: {path}")
    labels: dict[int, str] = {}
    for raw_key, raw_value in cast(dict[object, object], parsed).items():
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        try:
            channel_id = int(str(raw_key))
        except ValueError:
            continue
        labels[channel_id] = raw_value.strip()
    return labels


if __name__ == "__main__":
    main()
