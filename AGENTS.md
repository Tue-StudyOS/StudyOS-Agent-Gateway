# Repository Instructions

- Keep files focused and roughly in the 300-lines-or-less ballpark; treat this
  as a strong modularity signal, not a hard mechanical limit.
- Do not add fallback credentials, mock production data, or silent failure paths.
- Prefer explicit errors for missing Discord, GitHub, or agent configuration.
- Keep tokens, webhook secrets, logs, and Discord/GitHub IDs with personal data out of commits.
- Use typed boundaries for Discord events, GitHub payloads, and agent requests.
- Follow naming, formatting, and architectural patterns already present in the codebase.
- Use the available development cycle: formatters, linters, hooks, CI/GitHub Actions,
  and tests. Add or update focused tests for features and fixes.
- In PRs and reviews, surface security, privacy, credential-handling, reliability, and
  operational concerns before they become production risks.
- For complex tasks, use runtime subagents/delegation when available. Split independent
  work into clear subtasks with disjoint file ownership, then integrate and review the
  results before replying.
- For parallel implementation work, prefer isolated git worktrees so concurrent agents do
  not edit the same checkout. Use task- or channel-specific branch names and keep local
  worktree directories ignored.
- Codex automations are TOML/Markdown files: active jobs live under
  `$CODEX_HOME/automations/<automation-id>/automation.toml` with optional
  `$CODEX_HOME/automations/<automation-id>/memory.md`; reviewable templates live under
  `$CODEX_HOME/automation-templates/<template-id>/` and repo seed content lives under
  `codex/`.
- When asked to create or adjust an automation, edit those automation TOML/Markdown files
  or use Codex app automation tooling. Do not add helper scripts or daemons unless the
  user explicitly asks for that implementation.
- Do not add `Co-authored-by`, `Generated-by`, or similar agent attribution trailers to
  commits, PR bodies, issue comments, or release notes unless a human explicitly asks.
  Keep GitHub attribution on the authenticated repository user; do not list Codex,
  StudyOS Agent Gateway, or other agent runtimes as contributors.
- To update an existing automation, inspect `$CODEX_HOME/automations/*/automation.toml`
  and `$CODEX_HOME/automation-templates/*/automation.toml`, preserve unrelated fields,
  and change `status`, `rrule`, `prompt`, or adjacent `memory.md` as requested.
- Persist important recurring project or course learnings in
  `$CODEX_HOME/memories/studyos-course.md` under a dated "Runtime Learnings" note. For
  target-repository-specific conventions, create gitignored `.learnings/` or `.journal/`
  Markdown files in that repository. Do not store secrets, credentials, private personal
  data, or noisy one-off conversation details.
- Run `ruff check .`, `pyright`, and `pytest` before claiming changes are ready.
