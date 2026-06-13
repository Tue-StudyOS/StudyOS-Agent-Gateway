# Setup Guide

## Discord

1. Create an application in the Discord Developer Portal.
2. Add a bot user and copy the bot token into `DISCORD_TOKEN`.
3. Enable the message content intent.
4. Invite the bot with the `bot` scope.
4. Copy the target PR channel ID into `DISCORD_PR_CHANNEL_ID` if you want GitHub notification mirrors or poller summaries in Discord.
5. Optionally copy the server ID into `DISCORD_GUILD_ID` so old slash commands can be cleared quickly.

StudyOS interaction is mention-first. Participants tag the bot in Discord when they want to brainstorm, ask for research, refine an issue, or start a scoped task.

## GitHub

1. Create a webhook on the monorepo.
2. Set the payload URL to `https://<host>/webhooks/github`.
3. Set content type to `application/json`.
4. Generate a random secret and put it in both GitHub and `GITHUB_WEBHOOK_SECRET`.
5. Subscribe to pull request, issue, and issue comment events.

Webhooks are optional. The simpler deployment is to authenticate `gh` and Codex in the container, then let Codex poll and navigate GitHub with the CLI on a schedule. Keep `GITHUB_TOKEN` only as a non-interactive read fallback.

For a first Discord-only smoke test, you only need `DISCORD_TOKEN`, `DISCORD_MESSAGE_AGENT_ENABLED=true`, and either `AGENT_COMMAND` or `AGENT_WEBHOOK_URL`.

## Deployment

Run locally:

```bash
studyos-agent-gateway
```

Run with Docker Compose:

```bash
docker compose up --build -d
```

Expose port `8080` through a reverse proxy or tunnel for GitHub webhooks.

For the course Jetson, use the source-sync deployment script instead of
baking credentials into an image layer:

```bash
IMAGE_TAG=studyos-agent-gateway:jetson-$(date -u +%Y%m%d%H%M%S) \
  scripts/deploy_jetson.sh
```

The script syncs the repository to a remote build directory, builds
`Dockerfile.agent` on the Jetson with host networking, and recreates the
`studyos-agent-gateway` container. It preserves runtime state in named Docker
volumes for `/auth/codex`, `/auth/gh`, `/auth/gh-public`,
`/auth/gh-studyos-org`, `/workspaces`, `/tmp/studyos-artifacts`, and
`/tmp/studyos-discord-attachments`.

Seed additional GitHub CLI profiles only through stdin:

```bash
scripts/seed_jetson_studyos_org_gh.sh < /path/to/tue-studyos-token.txt
scripts/seed_jetson_public_gh.sh < /path/to/public-repo-token.txt
```

The StudyOS organization profile is mounted at `/auth/gh-studyos-org` and is
for repositories under `Tue-StudyOS/*`. The public profile is mounted at
`/auth/gh-public` and should only be used for public open-source contribution
flows where the fine-grained profiles cannot push to a fork.

## Agent Runtime On The Server

The recommended StudyOS setup is:

1. Provision a small server or VM.
2. Deploy the gateway harness; repositories can be cloned on demand into the
   persistent `/workspaces` volume.
3. Deploy the agent image, which includes `gh`, `git`, SSH, Node/npm, and Codex.
4. Authenticate `gh` and Codex inside the running container.
5. Deploy this bot with `AGENT_COMMAND` pointing to the authenticated runtime.
6. Keep implementation starts, approvals, and merges behind human review.

This lets the course share a few authenticated coding-agent instances instead of requiring every participant to own and configure one. Discord and GitHub become the common StudyOS interface.

The image contains tooling, but authentication belongs in Docker volumes, mounted config directories, or runtime environment variables. Never build tokens into the image.

For Codex and GitHub CLI auth inside the container:

```bash
AGENT_COMMAND="codex exec --json --dangerously-bypass-approvals-and-sandbox --cd /workspaces -"
AGENT_WORKDIR=/workspaces
AGENT_DISCORD_WORKTREE_ROOT=/workspaces/.studyos-discord-worktrees
docker compose -f docker-compose.agent.yml up --build -d
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway gh auth login
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway codex login
```

The provided agent image installs Node from the official Node image, GitHub CLI
from GitHub's apt repository, Graphviz for rendered diagrams, and
`@openai/codex` through npm. If your agent needs compilers, browser tooling,
CUDA tools, or course-specific system packages, extend `Dockerfile.agent` for
that course environment.

For richer Discord interactions, generated files should be written under
`/tmp/studyos-artifacts` or `/workspaces`. The bot validates artifact paths
against `DISCORD_ARTIFACT_ALLOWED_ROOTS` before upload. Start proactive
Discord participation with `DISCORD_PROACTIVE_DRY_RUN=true`; only set it to
`false` after testing in a low-risk server.

For Claude Code, run it directly on the host or build a sibling image with the Claude CLI installed:

```bash
AGENT_COMMAND="claude -p --permission-mode acceptEdits"
AGENT_WORKDIR=/workspaces
```

Use `AGENT_AUTO_REVIEW_ENABLED=true` only after mention-based agent usage works
reliably. Webhook-triggered agent runs do not require `DISCORD_PR_CHANNEL_ID`;
set that channel only when webhook notifications or poller summaries should be
mirrored into Discord.

## Periodic GitHub Triage

Set:

```bash
GITHUB_POLL_ENABLED=true
GITHUB_POLL_INTERVAL_SECONDS=1800
GITHUB_POLL_LIMIT=20
```

The bot will periodically list open PRs and issues, build one prompt, and invoke the agent. This is the safest shape for "every 15 or 30 minutes, check comments/issues/PRs and summarize" because scheduling remains outside Discord message handling and can be disabled independently.

When `GITHUB_TOKEN` is not set, the poller uses `gh auth token`, so the container's `gh auth login` session is enough. For more advanced scheduled work, Codex automations can skip the bot poller entirely and directly prompt Codex to inspect the repository with `gh`.

Unattended triage should refine issues, surface duplicates, summarize stale PRs, and invite review attention. It should not start implementation by itself. A student should explicitly ask the bot to implement a specific issue in Discord or GitHub before it creates a branch or PR.

## External Agent Runtime

Set `AGENT_WEBHOOK_URL` to a service that accepts:

```json
{
  "prompt": "summarize open PRs",
  "source": "discord",
  "user": "name#0000",
  "channel_id": 123
}
```

The service must respond with:

```json
{"message": "agent response"}
```
