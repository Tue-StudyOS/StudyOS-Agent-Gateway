from pathlib import Path

from study_discord_agent.memory import ensure_studyos_memory
from study_discord_agent.prompt_context import build_agent_prompt


def test_ensure_studyos_memory_creates_default_entrypoint(tmp_path: Path) -> None:
    path = ensure_studyos_memory(str(tmp_path))
    text = path.read_text()

    assert path.name == "studyos-course.md"
    assert "Build your own StudyOS" in text
    assert "Modern Agentic" in text


def test_build_agent_prompt_points_to_memory(tmp_path: Path) -> None:
    prompt = build_agent_prompt("list tickets", "student", 123, str(tmp_path))

    assert str(tmp_path / "memories" / "studyos-course.md") in prompt
    assert "User request:\nlist tickets" in prompt
