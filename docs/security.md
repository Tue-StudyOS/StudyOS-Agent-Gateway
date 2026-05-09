# Security Notes

The bot can become powerful once GitHub writes are enabled. Keep the first deployment conservative.

## Defaults

- `GITHUB_WRITE_ENABLED=false` disables PR comments, issue closure, and merges.
- GitHub webhook payloads require HMAC verification.
- Slash command writes can be restricted by Discord role IDs.
- The bot does not read arbitrary Discord messages.

## Recommended GitHub Token

Use a fine-grained token or GitHub App installation token scoped to the course monorepo.

Grant only what is needed:

- Metadata: read
- Issues: read/write only if comments or issue closure are enabled
- Pull requests: read/write only if merging is enabled

## Operational Rules

- Rotate Discord and GitHub tokens if they are pasted into chat or logs.
- Keep `.env` out of commits.
- Do not expose the webhook endpoint without a secret.
- Keep merge commands role-restricted.
- Prefer a branch protection rule so the bot cannot bypass required reviews or checks.
