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
- Run `ruff check .`, `pyright`, and `pytest` before claiming changes are ready.
