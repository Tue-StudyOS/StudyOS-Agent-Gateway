from pathlib import Path

import pytest

from study_discord_agent.artifacts import parse_agent_reply
from study_discord_agent.discord_files import sanitize_filename, validate_artifact_files


def test_parse_agent_reply_supports_artifact_protocol() -> None:
    parsed = parse_agent_reply(
        '{"message":"diagram ready","files":["/tmp/studyos-artifacts/flow.png"]}'
    )

    assert parsed.message == "diagram ready"
    assert parsed.files == (Path("/tmp/studyos-artifacts/flow.png"),)


def test_parse_agent_reply_extracts_local_markdown_file_link() -> None:
    parsed = parse_agent_reply(
        "Done: [studyos_dummy_latex_presentation.pdf]"
        "(/workspace/output/studyos_dummy_latex_presentation.pdf)"
    )

    assert parsed.message == "Done: studyos_dummy_latex_presentation.pdf (attached)"
    assert parsed.files == (Path("/workspace/output/studyos_dummy_latex_presentation.pdf"),)


def test_parse_agent_reply_keeps_local_source_link_with_line_number() -> None:
    text = "See [README.md](/workspaces/Tue-StudyOS/StudyOS_Agent/README.md:19)."

    parsed = parse_agent_reply(text)

    assert parsed.message == text
    assert parsed.files == ()


def test_parse_agent_reply_extracts_bare_local_file_path_once() -> None:
    parsed = parse_agent_reply(
        "Attached /workspace/output/studyos_dummy_presentation.pdf directly: "
        "/workspace/output/studyos_dummy_presentation.pdf"
    )

    assert parsed.files == (Path("/workspace/output/studyos_dummy_presentation.pdf"),)


def test_parse_agent_reply_keeps_bare_local_source_path_with_line_number() -> None:
    parsed = parse_agent_reply("See /workspaces/Tue-StudyOS/StudyOS_Agent/README.md:19.")

    assert parsed.files == ()


def test_parse_agent_reply_leaves_normal_json_alone() -> None:
    parsed = parse_agent_reply('{"answer": "plain JSON"}')

    assert parsed.message == '{"answer": "plain JSON"}'
    assert parsed.files == ()


def test_sanitize_filename_removes_path_and_control_chars() -> None:
    assert sanitize_filename("../weird file?.png") == "weird_file_.png"


def test_validate_artifact_files_rejects_disallowed_roots(tmp_path: Path) -> None:
    artifact = tmp_path / "flow.png"
    artifact.write_bytes(b"png")

    with pytest.raises(RuntimeError, match="outside allowed roots"):
        validate_artifact_files((artifact,), (Path("/tmp/studyos-artifacts"),), 8_000_000)


def test_validate_artifact_files_accepts_allowed_file(tmp_path: Path) -> None:
    artifact = tmp_path / "flow.png"
    artifact.write_bytes(b"png")

    assert validate_artifact_files((artifact,), (tmp_path,), 8_000_000) == (artifact.resolve(),)
