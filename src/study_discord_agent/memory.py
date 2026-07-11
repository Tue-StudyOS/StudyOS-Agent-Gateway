from hashlib import sha256
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
MANAGED_BLOCK_IDS = {
    "## Communication style": "communication-style",
    "## Proactive Discord Participation": "proactive-discord",
    "## Discord Behavior": "discord-behavior",
}
LEGACY_SECTION_SHA256 = {
    "## Communication style": {"26bfda65996aacbbb1e63fbf31d7cabb227a0a92145757347e7d2db46b0b5210"},
    "## Proactive Discord Participation": {
        "5f27099b6ff75af0f06f433c477639feb4eb30d8c1d2255391544003f3b3ad4b",
        "a3d3a83e4a52b47f08f4e715ddfff4aa7444cde599e6b2e37f7c6fd18a64f491",
    },
    "## Discord Behavior": {"d2acb4a77e2ee3c61a77d80618b984f3aac96f7b56871b56669b601c5bcf4006"},
}


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
    refreshed = _upsert_managed_block(text, DEFAULT_GLOBAL_AGENTS, "## Communication style")
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
        "## Discord Behavior",
    ):
        refreshed = _upsert_managed_block(refreshed, default_memory, heading)
    for heading in (
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


def _upsert_managed_block(text: str, default_memory: str, heading: str) -> str:
    block_id = MANAGED_BLOCK_IDS[heading]
    start_marker = f"<!-- studyos-managed:{block_id}:start -->"
    end_marker = f"<!-- studyos-managed:{block_id}:end -->"
    default_section = _extract_section(default_memory, heading)
    block_start = default_section.index(start_marker)
    block_end = default_section.index(end_marker) + len(end_marker)
    managed_block = default_section[block_start:block_end]

    if start_marker in text and end_marker in text:
        current_section = _extract_section(text, heading)
        legacy_prefix = current_section[: current_section.index(start_marker)].rstrip()
        if _is_legacy_section(heading, legacy_prefix):
            return _replace_section(text, heading, default_section)
        start = text.index(start_marker)
        end = text.index(end_marker, start) + len(end_marker)
        return text[:start] + managed_block + text[end:]
    if heading not in text:
        return _insert_before_heading(text, default_section, "## GitHub Workflow")
    if _is_legacy_section(heading, _extract_section(text, heading)):
        return _replace_section(text, heading, default_section)

    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text.rstrip() + "\n\n" + managed_block + "\n"
    return text[:next_heading].rstrip() + "\n\n" + managed_block + text[next_heading:]


def _is_legacy_section(heading: str, section: str) -> bool:
    digest = sha256(section.strip().encode()).hexdigest()
    return digest in LEGACY_SECTION_SHA256[heading]


def _replace_section(text: str, heading: str, replacement: str) -> str:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text[:start].rstrip() + "\n\n" + replacement + "\n"
    return text[:start].rstrip() + "\n\n" + replacement + text[next_heading:]


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
