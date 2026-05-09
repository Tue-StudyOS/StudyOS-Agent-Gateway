# Security Notes

The gateway can become powerful once the agent runtime has authenticated GitHub writes. Keep the first deployment conservative.

## Defaults

- The Python bot exposes no PR merge command and no issue close command.
- GitHub webhook payloads require HMAC verification.
- Discord collaboration is mention-gated.
- PR merges are human-only through GitHub.

## Recommended GitHub Token

Prefer `gh auth login` in the deployment container for the interactive agent-server setup. Use a fine-grained token or GitHub App installation token only for non-interactive read polling.

Grant only what is needed:

- Metadata: read
- Issues: read for polling
- Pull requests: read for polling

## Operational Rules

- Rotate Discord and GitHub tokens if they are pasted into chat or logs.
- Keep `.env` out of commits.
- Do not expose the webhook endpoint without a secret.
- Prefer a branch protection rule so the agent runtime cannot bypass required reviews or checks.
- Do not grant bypass permissions to the authenticated CLI account.
