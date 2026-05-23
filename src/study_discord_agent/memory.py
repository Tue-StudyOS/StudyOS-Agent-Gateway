from pathlib import Path

from study_discord_agent.guidance import (
    AUTOMATION_MEMORY_SECTION,
    DEFAULT_GLOBAL_AGENTS,
    GLOBAL_AUTOMATION_SECTION,
    GLOBAL_LEARNING_SECTION,
)

STUDYOS_MEMORY_FILENAME = "studyos-course.md"
GLOBAL_AGENTS_FILENAME = "AGENTS.md"
REPO_STUDYOS_MEMORY_PATH = Path("codex") / "memories" / STUDYOS_MEMORY_FILENAME


def read_default_studyos_memory() -> str:
    candidates = (
        Path.cwd() / REPO_STUDYOS_MEMORY_PATH,
        Path(__file__).resolve().parents[2] / REPO_STUDYOS_MEMORY_PATH,
    )
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    paths = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"StudyOS memory seed missing; checked: {paths}")


def get_studyos_memory_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / "memories" / STUDYOS_MEMORY_FILENAME


def get_global_agents_path(codex_home: str | None) -> Path:
    root = Path(codex_home or "~/.codex").expanduser()
    return root / GLOBAL_AGENTS_FILENAME


def ensure_global_agents(codex_home: str | None) -> Path:
    path = get_global_agents_path(codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_refresh_global_agents(DEFAULT_GLOBAL_AGENTS), encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        path.write_text(_refresh_global_agents(text), encoding="utf-8")
    return path


def ensure_studyos_memory(codex_home: str | None) -> Path:
    path = get_studyos_memory_path(codex_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(read_default_studyos_memory(), encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        path.write_text(_refresh_studyos_memory(text), encoding="utf-8")
    return path


def _refresh_studyos_memory(text: str) -> str:
    if "# StudyOS Agent Memory" not in text:
        return read_default_studyos_memory()
    return _upsert_managed_sections(text)


def _refresh_global_agents(text: str) -> str:
    if "# Global Codex Guidance" not in text:
        return text
    refreshed = text
    if "## Codex Automations" not in refreshed:
        refreshed = refreshed.rstrip() + "\n\n" + GLOBAL_AUTOMATION_SECTION
    if "## Persistent Learnings" not in refreshed:
        refreshed = refreshed.rstrip() + "\n\n" + GLOBAL_LEARNING_SECTION
    return refreshed


def _upsert_managed_sections(text: str) -> str:
    refreshed = text
    default_memory = read_default_studyos_memory()
    for heading in (
        "## Proactive Discord Participation",
        "## Product Discovery And Reuse",
        "## Persistent Learnings",
        "## Delivery Lifecycle",
    ):
        if heading not in refreshed:
            refreshed = _insert_before_heading(
                refreshed,
                _extract_section(default_memory, heading),
                "## GitHub Workflow",
            )
    return _upsert_automation_section(refreshed)


def _extract_section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text[start:].strip()
    return text[start:next_heading].strip()


def _insert_before_heading(text: str, section: str, before_heading: str) -> str:
    if before_heading not in text:
        return text.rstrip() + "\n\n" + section + "\n"
    index = text.index(before_heading)
    return text[:index].rstrip() + "\n\n" + section + "\n\n" + text[index:].lstrip()


def _upsert_automation_section(text: str) -> str:
    heading = "## Codex Runtime And Automations"
    if heading not in text:
        return text.rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION

    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text[:start].rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION
    return text[:start].rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION + text[next_heading:]
