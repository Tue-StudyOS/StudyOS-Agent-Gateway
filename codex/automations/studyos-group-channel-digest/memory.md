# StudyOS group channel digest

This automation is intended for Codex app automation runners, not plain CLI
startup hooks.

Run a daily human-in-the-loop summary review for StudyOS Discord channels named
`group-*`.

Behavior:
- Generate proposed summaries from `group-*` channels only.
- Post proposals back into the source group channel first.
- Share to `#updates` only after a non-bot group channel member explicitly
  approves the pending proposal.
- Do not interrupt ongoing channel engagement with a new proposal; schedule one
  deferred proposal check between the hold-off time and the next cron fire.

Human policy:
- Students approve and merge pull requests.
- Implementation starts only after a human explicitly asks for it in Discord or
  a GitHub issue comment.
- Do not close issues or PRs unless explicitly asked.
- Prefer issue refinement and PR review summaries before implementation.
