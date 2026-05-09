# Agent Runtime Design

The bot is a gateway, not the agent itself. It receives Discord and GitHub events, builds a prompt, and sends that prompt to one configured runtime.

## Local CLI Mode

Use `AGENT_COMMAND` when the bot and agent run on the same server or inside the same container.

The prompt is sent through stdin. This avoids shell interpolation of untrusted Discord text.

```bash
AGENT_COMMAND="codex exec --dangerously-bypass-approvals-and-sandbox --cd /workspace -"
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

## Automatic GitHub Follow-Up

Set `AGENT_AUTO_REVIEW_ENABLED=true` to run the agent for useful GitHub webhook events.

The generated prompts ask for PR review summaries, issue refinement questions, duplicate detection, and next steps. They explicitly tell the agent not to merge pull requests. GitHub write access should still be controlled by:

- `gh auth login` scopes
- branch protection
- human-only merge policy

## Cron And Scheduled Work

For this repo, prefer the built-in GitHub poller first:

```bash
GITHUB_POLL_ENABLED=true
GITHUB_POLL_INTERVAL_SECONDS=1800
```

Use host cron, systemd timers, GitHub Actions, or a separate scheduler container only for jobs that should run independently from the bot process.

Codex-managed automations are a good fit for detached StudyOS repository maintenance. In that setup, the bot handles Discord and webhook intake, while a Codex cron job runs every 15 or 30 minutes and prompts Codex to inspect the StudyOS course monorepo with `gh`:

```text
Use the authenticated GitHub CLI to inspect open issues, pull requests,
and recent review comments in owner/repo. Identify duplicates, blocked
threads, stale PRs, and small implementation tasks. Comment only when
there is a useful update. Create branches and PRs for clearly scoped
fixes. Do not merge or close work unless repository policy allows it.
```

For StudyOS, prefer this simpler Codex automation when it is enough. Webhooks are only needed for low-latency updates into Discord or immediate issue-refinement prompts. The bot-side poller remains useful when the result should post into Discord or reuse the same agent command configured for Discord mentions.

## Authentication Model

The deployable image should include the bot and any CLIs you need. It should not include credentials.

Use runtime injection instead:

- `DISCORD_TOKEN` as an environment variable or secret
- GitHub CLI auth in the `gh-auth` Docker volume, or `GITHUB_TOKEN` as a fallback
- `CODEX_HOME` in the `codex-auth` Docker volume, or mounted from a host auth directory
- Claude Code auth mounted or configured according to its deployment mode
- SSH deploy keys mounted read-only if the agent needs Git over SSH

## Interactive Container Login

The default agent compose file creates persistent `gh-auth` and `codex-auth` volumes. Log in once:

```bash
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway gh auth login
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway codex login
```

After that, Discord mentions, GitHub webhooks, and the GitHub poller can invoke the authenticated tools without embedding tokens in the image.
