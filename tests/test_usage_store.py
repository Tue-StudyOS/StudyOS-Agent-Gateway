import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from study_discord_agent.codex_command import AgentUsage
from study_discord_agent.usage_plot import render_usage_dot, write_usage_png
from study_discord_agent.usage_report import render_usage_report
from study_discord_agent.usage_store import ChannelUsageStore, default_usage_store_path


def test_channel_usage_store_accumulates(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    store = ChannelUsageStore(path)

    store.add(
        123,
        AgentUsage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=8,
            reasoning_output_tokens=2,
        ),
        "session-a",
    )
    store.add(
        123,
        AgentUsage(
            input_tokens=50,
            cached_input_tokens=10,
            output_tokens=6,
            reasoning_output_tokens=1,
        ),
        "session-a",
    )

    row = ChannelUsageStore(path).rows()[0]
    assert row.channel_id == 123
    assert row.turns == 2
    assert row.input_tokens == 150
    assert row.cached_input_tokens == 50
    assert row.output_tokens == 14
    assert row.reasoning_output_tokens == 3
    assert row.total_tokens == 164
    assert row.last_session_id == "session-a"


def test_usage_report_renders_leaderboard(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    store = ChannelUsageStore(path)
    store.add(1, AgentUsage(input_tokens=10, output_tokens=2), "session-a")
    store.add(2, AgentUsage(input_tokens=30, output_tokens=4), "session-b")

    report = render_usage_report(path, limit=10)

    assert "channel_id turns total_tokens" in report
    assert report.splitlines()[1].startswith("2 1 34 ")
    assert report.splitlines()[2].startswith("1 1 12 ")


def test_usage_plot_writes_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "usage.json"
    output = tmp_path / "usage.png"
    store = ChannelUsageStore(path)
    store.add(1, AgentUsage(input_tokens=10, output_tokens=2), "session-a")
    store.add(2, AgentUsage(input_tokens=30, output_tokens=4), "session-b")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = cast(BinaryIO, kwargs["stdout"])
        stdout.write(b"\x89PNG\r\n\x1a\n")
        return subprocess.CompletedProcess(["dot", "-Tpng"], 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    rendered = render_usage_dot(path, limit=10)
    written = write_usage_png(path, output, limit=10)

    assert rendered.startswith("digraph usage")
    assert "StudyOS Codex Usage by Discord Channel" in rendered
    assert ">2</TD>" in rendered
    assert written == output
    assert output.read_bytes().startswith(b"\x89PNG")


def test_default_usage_store_lives_under_codex_home(tmp_path: Path) -> None:
    assert default_usage_store_path(str(tmp_path)) == (
        tmp_path / "gateway" / "discord-channel-usage.json"
    )
