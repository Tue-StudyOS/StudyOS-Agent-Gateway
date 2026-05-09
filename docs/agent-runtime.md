# Agent Runtime Design

The bot is a gateway, not the agent itself. It receives Discord and GitHub events, builds a prompt, and sends that prompt to one configured runtime.

## Local CLI Mode

Use `AGENT_COMMAND` when the bot and agent run on the same server or inside the same container.

The prompt is sent through stdin. This avoids shell interpolation of untrusted Discord text.

```bash
AGENT_COMMAND="codex exec --full-auto --cd /workspace -"
AGENT_WORKDIR=/workspace
```

Other examples:

```bash
AGENT_COMMAND="claude -p --permission-mode acceptEdits"
AGENT_COMMAND="/opt/openclaw/bin/openclaw run --stdin"
AGENT_COMMAND="/opt/picoclaw/bin/picoclaw run --stdin"
```

## Webhook Mode

Use `AGENT_WEBHOOK_URL` when the agent runs as a separate service. The bot sends a JSON payload and expects a JSON reply.

```json
{
  "prompt": "review pull request 12",
  "source": "discord",
  "user": "student",
  "channel_id": 123
}
```

Expected response:

```json
{"message": "summary to post back to Discord"}
```

## Automatic PR Reviews

Set `AGENT_AUTO_REVIEW_ENABLED=true` to run the agent for `opened`, `ready_for_review`, and `synchronize` pull request events.

The generated prompt asks for a Discord summary and explicitly tells the agent not to merge or close anything unless instructed. GitHub write access should still be controlled by:

- `GITHUB_WRITE_ENABLED`
- fine-grained token scopes
- Discord role allowlist
- repository branch protection

## Cron And Scheduled Work

For this repo, prefer the built-in GitHub poller first:

```bash
GITHUB_POLL_ENABLED=true
GITHUB_POLL_INTERVAL_SECONDS=1800
```

Use host cron, systemd timers, GitHub Actions, or a separate scheduler container only for jobs that should run independently from the bot process.

## Authentication Model

The deployable image should include the bot and any CLIs you need. It should not include credentials.

Use runtime injection instead:

- `DISCORD_TOKEN` as an environment variable or secret
- `GITHUB_TOKEN` as an environment variable or GitHub App token provider
- `CODEX_HOME` mounted read-only for Codex auth
- Claude Code auth mounted or configured according to its deployment mode
- SSH deploy keys mounted read-only if the agent needs Git over SSH
