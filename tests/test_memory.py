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
    assert "Proactive Discord Participation" in text
    assert "Product Discovery And Reuse" in text
    assert "Persistent Learnings" in text
    assert "Delivery Lifecycle" in text
    assert "student-provided\n  repository URL" in text
    assert "/workspaces/Tue-StudyOS/<repo-name>" in text
    assert "/workspaces/.studyos-discord-worktrees/<channel-or-thread-id>" in text
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
    assert "paused automations" in text


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
    assert "/workspaces/Tue-StudyOS/<repo-name>" in text
    assert "share URLs\nin Discord or GitHub" in text
    assert "Discord thread IDs are channel IDs" in text
    assert "Match the response language" in text
    assert "edit that same message" in text
    assert "$CODEX_HOME/memories/studyos-course.md" in text
    assert "do not route student credentials" in text
    assert "do not silently reject or skip" in text
    assert "unrelated changes" in text
    assert "logical commit groups" in text
    assert "Codex <codex@openai.com>" in text
    assert "Do not add `Co-authored-by`, `Generated-by`" in text
    assert "GitHub PRs, issue comments, and review comments" in text
    assert "without adding extra runtime attribution trailers" in text
    assert "specification sheets" in text
    assert "unnecessary\ncompute cost" in text
    assert "test-driven development where practical" in text
    assert "/workspaces/.studyos-discord-worktrees/<channel-or-thread-id>" in text
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
    assert "Codex Automations" in text
    assert "$CODEX_HOME/automations/<automation-id>/automation.toml" in text
    assert "To pause or activate an automation, change `status`" in text
    assert "$CODEX_HOME/memories/studyos-course.md" in text
    assert "Runtime Learnings" in text
    assert "Do not store secrets" in text
    assert "Persistent Learnings" in text


def test_ensure_global_agents_preserves_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("custom global guidance\n", encoding="utf-8")

    ensured_path = ensure_global_agents(str(tmp_path))

    assert ensured_path == path
    assert path.read_text(encoding="utf-8") == "custom global guidance\n"


def test_ensure_global_agents_refreshes_generated_guidance(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Global Codex Guidance\n\nExisting generated guidance.\n", encoding="utf-8")

    ensure_global_agents(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert "Existing generated guidance." in text
    assert "Codex Automations" in text
    assert "$CODEX_HOME/automations/*/automation.toml" in text
    assert "Persistent Learnings" in text
    assert "Runtime Learnings" in text


def test_build_agent_prompt_points_to_memory(tmp_path: Path) -> None:
    prompt = build_agent_prompt("list tickets", "student", 123, str(tmp_path), 456)

    assert str(tmp_path / "memories" / "studyos-course.md") in prompt
    assert "Discord source message id: 456" in prompt
    assert "studyos-discord-context --channel-id <channel_id>" in prompt
    assert "GitHub auth routing" in prompt
    assert "GH_STUDYOS_ORG_CONFIG_DIR=/auth/gh-studyos-org" in prompt
    assert "Tue-StudyOS" in prompt
    assert "full name starts with `Tue-StudyOS/`" in prompt
    assert "/workspaces/Tue-StudyOS/<repo-name>" in prompt
    assert "clone or fetch missing" in prompt
    assert "GH_PUBLIC_CONFIG_DIR=/auth/gh-public" in prompt
    assert "classic token with only `public_repo`" in prompt
    assert "fork the upstream repository" in prompt
    assert "studyos-usage-report --limit 20" in prompt
    assert "studyos-usage-plot --limit 20" in prompt
    assert "/tmp/studyos-artifacts/discord-channel-usage.png" in prompt
    assert "Discord displays SVG files as code/plaintext previews" in prompt
    assert "send files/images" in prompt
    assert "send to the current Discord channel id above" in prompt
    assert "Discord thread ids are channel ids" in prompt
    assert "Match the response language" in prompt
    assert "edit that same message" in prompt
    assert "Always attach files" in prompt
    assert "local paths are not usable in Discord" in prompt
    assert "Never print or commit the token" in prompt
    assert "Codex <codex@openai.com>" in prompt
    assert "Do not add Co-authored-by, Generated-by" in prompt
    assert "GitHub PRs, issue comments, and review comments" in prompt
    assert "without adding extra runtime attribution trailers" in prompt
    assert "isolated git worktrees" in prompt
    assert "/workspaces/.studyos-discord-worktrees/<channel-or-thread-id>" in prompt
    assert "originating Discord channel/thread id above" in prompt
    assert "subagents or delegation tools" in prompt
    assert "User request:\nlist tickets" in prompt


def test_build_agent_prompt_includes_runtime_workspace(tmp_path: Path) -> None:
    runtime_workspace = tmp_path / "discord-worktrees" / "123" / "example"
    prompt = build_agent_prompt(
        "implement issue",
        "student",
        123,
        str(tmp_path),
        456,
        (),
        str(runtime_workspace),
    )

    assert f"Runtime workspace for this Discord request:\n{runtime_workspace}" in prompt
    assert "Start implementation from this workspace" in prompt


def test_build_agent_prompt_allows_non_discord_source(tmp_path: Path) -> None:
    prompt = build_agent_prompt("review issue", "github-webhook", None, str(tmp_path))

    assert "Discord user: github-webhook" in prompt
    assert "Discord channel id: none" in prompt
    assert "Discord source message id: unknown" in prompt


def test_ensure_studyos_memory_appends_missing_sections(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir()
    path = memory_dir / "studyos-course.md"
    path.write_text("# StudyOS Agent Memory\n\nLocal notes stay here.\n", encoding="utf-8")

    ensured_path = ensure_studyos_memory(str(tmp_path))
    text = ensured_path.read_text(encoding="utf-8")

    assert ensured_path == path
    assert "Local notes stay here." in text
    assert "Proactive Discord Participation" in text
    assert "Product Discovery And Reuse" in text
    assert "Persistent Learnings" in text
    assert "Delivery Lifecycle" in text
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
    assert "Product Discovery And Reuse" in text
    assert "Persistent Learnings" in text


def test_default_memory_includes_credential_policy(tmp_path: Path) -> None:
    path = ensure_studyos_memory(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    assert "local sidecars by" in text
    assert "proceed if they confirm" in text
    assert "what data appears obtainable" in text
    assert "ask whether the group\n  wants an issue/spec" in text
    assert "Runtime Learnings" in text
    assert "Match the response language" in text
    assert "edit that same message" in text
