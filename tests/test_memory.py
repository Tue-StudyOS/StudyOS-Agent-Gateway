from pathlib import Path

from study_discord_agent.memory import ensure_global_agents, ensure_studyos_memory
from study_discord_agent.prompt_context import build_agent_prompt


def test_ensure_studyos_memory_creates_default_entrypoint(tmp_path: Path) -> None:
    path = ensure_studyos_memory(str(tmp_path))
    text = path.read_text()

    assert path.name == "studyos-course.md"
    assert "Build your own StudyOS" in text
    assert "Modern Agentic" in text
    assert "experienced co-developer" in text
    assert "Discord-native thinking partner" in text
    assert "student-provided\n  repository URL" in text
    assert "Do not assume\n  the main wrapper repository" in text
    assert "lightweight specification sheets" in text
    assert "compute and maintenance costs" in text
    assert "official\n  documentation" in text
    assert "acceptance criteria rather than" in text
    assert "strong modularity target" in text
    assert "GitHub Actions" in text
    assert "credential-handling" in text
    assert "Codex Runtime And Automations" in text
    assert "Python heartbeat" not in text
    assert "automation templates" in text


def test_ensure_global_agents_creates_codex_home_guidance(tmp_path: Path) -> None:
    path = ensure_global_agents(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert path == tmp_path / "AGENTS.md"
    assert "StudyOS Discord/GitHub collaboration gateway" in text
    assert "Build your own StudyOS" in text
    assert "environment where the shared Codex agent" in text
    assert "belongs to the agent runtime" in text
    assert "image is a harness" in text
    assert "/workspaces" in text
    assert "share URLs\nin Discord or GitHub" in text
    assert "$CODEX_HOME/memories/studyos-course.md" in text
    assert "do not route student credentials" in text
    assert "do not silently reject or skip" in text
    assert "unrelated changes" in text
    assert "logical commit groups" in text
    assert "specification sheets" in text
    assert "unnecessary\ncompute cost" in text
    assert "test-driven development where practical" in text
    assert "acceptance criteria" in text
    assert "not a hard mechanical limit" in text
    assert "coherent\nnaming and formatting patterns" in text
    assert "CI/GitHub Actions" in text
    assert "short Discord-friendly answers" in text
    assert "keep the discussion flowing" in text
    assert "substantive work such as research" in text
    assert "helpful teammate and thinking partner" in text
    assert "never force memes" in text
    assert "what was verified" in text
    assert "humans approve and merge" in text


def test_ensure_global_agents_preserves_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("custom global guidance\n", encoding="utf-8")

    ensured_path = ensure_global_agents(str(tmp_path))

    assert ensured_path == path
    assert path.read_text(encoding="utf-8") == "custom global guidance\n"


def test_build_agent_prompt_points_to_memory(tmp_path: Path) -> None:
    prompt = build_agent_prompt("list tickets", "student", 123, str(tmp_path), 456)

    assert str(tmp_path / "memories" / "studyos-course.md") in prompt
    assert "Discord source message id: 456" in prompt
    assert "studyos-discord-context --channel-id <channel_id>" in prompt
    assert "send files/images" in prompt
    assert "Always attach files" in prompt
    assert "local paths are not usable in Discord" in prompt
    assert "Never print or commit the token" in prompt
    assert "isolated git worktrees" in prompt
    assert "subagents or delegation tools" in prompt
    assert "User request:\nlist tickets" in prompt


def test_ensure_studyos_memory_appends_missing_sections(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    path = memory_dir / "studyos-course.md"
    path.write_text("# StudyOS Agent Memory\n\nLocal notes stay here.\n", encoding="utf-8")

    ensured_path = ensure_studyos_memory(str(tmp_path))
    text = ensured_path.read_text(encoding="utf-8")

    assert ensured_path == path
    assert "Local notes stay here." in text
    assert "Codex Runtime And Automations" in text
    assert "Python heartbeat" not in text


def test_ensure_studyos_memory_repairs_incomplete_seed(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    path = memory_dir / "studyos-course.md"
    path.write_text("\n\n## Codex Runtime And Automations\n\nOld partial seed.\n", encoding="utf-8")

    ensure_studyos_memory(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert text.startswith("# StudyOS Agent Memory")
    assert "Discord-native thinking partner" in text
    assert "attach it in the Discord reply" in text


def test_default_memory_includes_credential_policy(tmp_path: Path) -> None:
    path = ensure_studyos_memory(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert "local sidecars by" in text
    assert "proceed if they confirm" in text
