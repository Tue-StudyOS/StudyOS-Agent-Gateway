from pathlib import Path

SEED_ROOT = Path("codex") / "skills"
EXPECTED_SKILL_IDS = {
    "find-skills",
    "studyos-quality-review",
    "studyos-self-improvement",
    "studyos-skill-expansion",
}


def _skill_paths() -> list[Path]:
    return sorted(SEED_ROOT.glob("*/SKILL.md"))


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    data: dict[str, str] = {}
    for line in lines[1:end]:
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def test_static_skills_exist_for_each_seed() -> None:
    assert {path.parent.name for path in _skill_paths()} == EXPECTED_SKILL_IDS

    for skill_id in EXPECTED_SKILL_IDS:
        assert (SEED_ROOT / skill_id / "agents" / "openai.yaml").exists()


def test_static_skills_have_codex_frontmatter() -> None:
    for path in _skill_paths():
        text = path.read_text(encoding="utf-8")
        frontmatter = _frontmatter(text)

        assert set(frontmatter) == {"name", "description"}
        assert frontmatter["name"] == path.parent.name
        assert frontmatter["description"]
        assert len(frontmatter["description"]) <= 500
        assert "[TODO" not in text
        assert len(text.splitlines()) <= 300


def test_skill_ui_metadata_mentions_skill_invocation() -> None:
    for skill_id in EXPECTED_SKILL_IDS:
        metadata = (SEED_ROOT / skill_id / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )

        assert f"Use ${skill_id}" in metadata


def test_agent_image_exposes_seeded_skills_via_codex_admin_path() -> None:
    dockerfile = Path("Dockerfile.agent").read_text(encoding="utf-8")

    assert "COPY codex ./codex" in dockerfile
    assert "mkdir -p /etc/codex/skills" in dockerfile
    assert "cp -R ./codex/skills/. /etc/codex/skills/" in dockerfile


def test_agent_image_pins_codex_cli_version() -> None:
    dockerfile = Path("Dockerfile.agent").read_text(encoding="utf-8")

    assert "ARG CODEX_VERSION=0.144.0" in dockerfile
    assert 'npm install -g "@openai/codex@${CODEX_VERSION}"' in dockerfile
