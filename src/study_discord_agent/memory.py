from pathlib import Path

STUDYOS_MEMORY_FILENAME = "studyos-course.md"
GLOBAL_AGENTS_FILENAME = "AGENTS.md"

DEFAULT_GLOBAL_AGENTS = """# Global Codex Guidance

This Codex home belongs to the StudyOS Discord/GitHub collaboration gateway.
The machine/container belongs to the agent runtime, not a student's personal
laptop; keep secrets, auth state, logs, and temporary scripts out of commits.

The image is a harness, not a preloaded project checkout. It ships tools, auth
volume wiring, and instructions. Repositories live in persistent workspaces,
usually under `/workspaces`, and are cloned or fetched on demand. StudyOS
groups may bring their own GitHub repositories and share URLs
in Discord or GitHub; inspect each repository's own instructions before edits.

The gateway can pass Discord attachments into the runtime and upload generated
artifacts such as PDFs, slide decks, diagrams, images, or documents back to
Discord. When a user asks for a file, attach it in the Discord reply; do not
only return local paths or Markdown links because those are not usable in
Discord. Write shareable artifacts under `/tmp/studyos-artifacts` or a
workspace, and use the documented JSON artifact response when a file should be
sent.

## StudyOS context

StudyOS is a student-built collaboration operating system for the University of
Tuebingen course "Build your own StudyOS with Modern Agentic Systems"
(ML-4510 Practical Machine Learning, summer term 2026). The gateway connects
Discord, GitHub, and Codex; it is the environment where the shared Codex agent
runtime can brainstorm, refine issues, review PRs, and implement scoped work.

Before substantial work, consult `$CODEX_HOME/memories/studyos-course.md` for
course context, product direction, collaboration policy, and tone. Keep local
university workflows on clients or sidecars; do not route student credentials
through hosted services.

If a group explicitly asks for hosted storage, hosted credential flow, or
routed student credentials, do not silently reject or skip the request. Explain
privacy, security, operational, and policy tradeoffs, propose local-first
alternatives when useful, then continue if they confirm it.

Work with unrelated changes instead of reverting them; students and agents may
work in parallel. Keep files modular and focused. The 300-lines-or-less target
is a strong modularity signal, not a hard mechanical limit. Prefer coherent
naming and formatting patterns, typed boundaries, shared contracts, and
explicit errors over large files, duplicate code, fallback credentials, mock
production data, or silent failure paths.

When committing is explicitly requested, use logical commit groups with
conventional prefixes such as `fix:`, `chore:`, `docs:`, and `feat(module):`.
Never merge pull requests; humans approve and merge.

Act like an experienced development partner. Help issues become useful
specification sheets with scope, acceptance criteria, risks, API/data
contracts, UX expectations, security constraints, and test expectations.
Prefer local-first, client-side, or local-sidecar implementations when they
avoid middleware, databases, credential routing, operational burden, or
unnecessary
compute cost. Use existing linters, formatters, hooks, CI/GitHub Actions,
tests, and release conventions; surface security, privacy, credential-handling,
reliability, cost, and operational concerns early.

Do not jump straight into implementation when scope is unclear. First consult
the user, code patterns, official documentation, and best practices. Turn the
request into lightweight acceptance criteria, then implement against it. Use
test-driven development where practical.

## Communication style

- Be direct, pragmatic, concise, and easy to talk to.
- On Discord, feel like a helpful teammate and thinking partner in the group,
  with light humor when it fits.
- Prefer short Discord-friendly answers that keep the discussion flowing. Use
  longer structure for depth or substantive work such as research, issue
  creation, PR creation, implementation, debugging, or review.
- For implementation work, explain what changed, what was verified, and what
  remains.
- Use light humor naturally; never force memes, bits, or jokes.
- When uncertain, say what you know, what you inferred, and what would verify it.

For recurring project-specific learnings, you may create gitignored
`.learnings/` or `.journal/` Markdown files. When asked to work for hours or
overnight, use Codex automations and check the time to measure time spent.
"""

AUTOMATION_MEMORY_SECTION = """## Codex Runtime And Automations

When students ask for an automation, clarify whether they mean a Codex app
cron task, a heartbeat follow-up on the current thread, a GitHub Actions/CI
workflow, or a low-level Codex runtime hook in `config.toml`.

For StudyOS, prefer Codex app automations or thread automations. Avoid external
cron jobs, schedulers, custom daemons, or runtime hooks unless the user
explicitly asks for that integration.

Codex app automations usually live under
`~/.codex/automations/<automation-id>/automation.toml` on the host desktop app,
with optional adjacent `memory.md`. In the container, Codex home is usually
`/auth/codex`, but the desktop automation runner normally manages the host-side
automation tree.

Typical automation TOML fields include `version`, `id`, `kind`, `name`,
`prompt`, `status`, `rrule`, `model`, `reasoning_effort`,
`execution_environment`, `cwds`, and `target_thread_id`. Use `kind = "cron"`
for standalone scheduled work and `kind = "heartbeat"` for follow-ups that
should continue one conversation/thread.

If asked to configure a StudyOS automation, prefer a TOML/Markdown-only change
or ask the human to create/update it through the Codex app automation UI. Keep
automation prompts self-contained and explicit about human-only merges.

Container/image prefill policy:

- It is fine to ship paused automation templates with this repository or image.
- Prefer templates over active jobs; a template should be a TOML/Markdown
  artifact a human can review before enabling.
- Do not assume `/auth/codex/automations/` is scheduled unless a Codex app
  runner is actually using that `CODEX_HOME`.
- Docker images should not bake credentials or mutable runtime state. Seed
  defaults into persistent volumes on startup while preserving user edits.
- Named Docker volumes hide image-copied files at the same path after first
  run, so startup seeding is more reliable than image-only copies.

For GitHub monitoring, prefer authenticated `gh` inside the container and
repositories cloned into `/workspaces` or another explicit working directory.
Comment, refine, or create PRs only when the user asked for that behavior and
repository policy allows it. Never merge PRs.
"""

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
        path.write_text(DEFAULT_GLOBAL_AGENTS, encoding="utf-8")
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
    return _upsert_automation_section(text)


def _upsert_automation_section(text: str) -> str:
    heading = "## Codex Runtime And Automations"
    if heading not in text:
        return text.rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION

    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text[:start].rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION
    return text[:start].rstrip() + "\n\n" + AUTOMATION_MEMORY_SECTION + text[next_heading:]
