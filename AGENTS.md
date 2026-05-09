# Repository Instructions

- Keep files focused and below 300 lines.
- Do not add fallback credentials, mock production data, or silent failure paths.
- Prefer explicit errors for missing Discord, GitHub, or agent configuration.
- Keep tokens, webhook secrets, logs, and Discord/GitHub IDs with personal data out of commits.
- Use typed boundaries for Discord events, GitHub payloads, and agent requests.
- Run `ruff check .`, `pyright`, and `pytest` before claiming changes are ready.
