import argparse
import os
from pathlib import Path

from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path


def render_usage_report(path: Path, limit: int) -> str:
    rows = ChannelUsageStore(path).rows()
    if not rows:
        return f"No usage recorded yet at {path}"

    header = (
        "channel_id turns total_tokens input_tokens cached_input_tokens "
        "output_tokens reasoning_output_tokens updated_at"
    )
    lines = [header]
    for row in rows[:limit]:
        lines.append(
            " ".join(
                [
                    str(row.channel_id),
                    str(row.turns),
                    str(row.total_tokens),
                    str(row.input_tokens),
                    str(row.cached_input_tokens),
                    str(row.output_tokens),
                    str(row.reasoning_output_tokens),
                    row.updated_at,
                ]
            )
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print StudyOS Codex token usage by channel.")
    parser.add_argument(
        "--path",
        type=Path,
        default=default_usage_store_path(os.environ.get("CODEX_HOME")),
        help="Usage JSON path. Defaults to $CODEX_HOME/gateway/discord-channel-usage.json.",
    )
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    print(render_usage_report(args.path, args.limit))


if __name__ == "__main__":
    main()
