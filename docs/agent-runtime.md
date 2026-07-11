# Agent Runtime Design

The bot is a gateway, not the agent itself. It receives Discord and GitHub events, builds a prompt, and sends that prompt to one configured runtime.

## Local CLI Mode

Use `AGENT_COMMAND` when the bot and agent run on the same server or inside the same container.

The prompt is sent through stdin. This avoids shell interpolation of untrusted Discord text.

```bash
AGENT_COMMAND="codex exec --json --dangerously-bypass-approvals-and-sandbox --cd /workspaces -"
AGENT_WORKDIR=/workspaces
```

The agent image is intentionally only the harness: Discord/GitHub gateway,
Codex CLI, GitHub CLI, auth volumes, and instructions. It does not need a
course repository baked in or mounted at build time. Repositories can be cloned
or fetched into the persistent `/workspaces` volume when students share GitHub
URLs or ask the agent to work on a project.

The agent image seeds `codex/config.toml` into `$CODEX_HOME/config.toml` so
every gateway invocation uses the same Codex profile:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
```

## Codex Local Providers

The default StudyOS agent runtime uses OpenAI-hosted Codex after
`codex login`. To run the same gateway harness against a local model server,
configure Codex in the mounted `$CODEX_HOME/config.toml` and include `--oss`
in the command:

```bash
AGENT_COMMAND="codex exec --oss --json --dangerously-bypass-approvals-and-sandbox --cd /workspaces -"
```

```toml
# Default local provider used when Codex runs with --oss.
oss_provider = "ollama" # or "lmstudio"
```

Keep provider and auth settings in user-level Codex config, which is
`$CODEX_HOME/config.toml` inside the container. Do not put provider overrides
only in a project-local `.codex/config.toml`; Codex ignores provider keys such
as `model_provider` and `model_providers` there. For custom providers or
remote-compatible model gateways, define them in the same user-level config and
select them with Codex's documented provider keys.

See the OpenAI Codex docs for
[OSS mode local providers](https://developers.openai.com/codex/config-advanced#oss-mode-local-providers)
and the
[config reference](https://developers.openai.com/codex/config-reference#configtoml).

The gateway also seeds `$CODEX_HOME/AGENTS.md` and
`$CODEX_HOME/memories/studyos-course.md` on startup if they do not already
exist. The canonical course memory is versioned at
`codex/memories/studyos-course.md`; the Docker runtime copies that Markdown into
Codex home. The global `AGENTS.md` carries reusable working agreements into the
Docker Codex runtime. Discord requests point Codex at the StudyOS memory entry
point instead of injecting the full course/project context into every prompt.

Discord requests include the source channel id and message id. The gateway does
not inject channel history into every prompt. Instead, the installed
`studyos-discord-context` helper is available to the local agent runtime:

```bash
studyos-discord-context --channel-id 123 --around-message-id 456 --limit 20
```

The prompt tells the agent to use that tool when a Discord mention refers to
earlier discussion or otherwise lacks enough context. The helper reads the bot
token from `DISCORD_TOKEN` and uses the Discord REST API, so it needs the bot to
have normal read-message permissions in the target channel.

The same prompt allows the local agent to write short temporary scripts using
`discord.py` or Discord REST when a user explicitly asks it to interact with
Discord. This is useful for sending generated files or images back to a channel.
The token is inherited from the gateway environment; agents must not print or
commit it, and should not send/edit/delete Discord content without a direct
human request.

## Discord Files And Diagrams

When a Discord mention includes attachments, the gateway saves them under
`DISCORD_ATTACHMENT_DIR` and lists the paths in the agent prompt. If the
attachment is an image and the configured command is `codex exec`, the gateway
also passes the image path with `-i` so Codex can inspect it directly.

Agents can ask the bot to upload generated files by returning a final JSON
message with `message` and `files`:

```json
{"message": "diagram ready", "files": ["/tmp/studyos-artifacts/flow.png"]}
```

The gateway also catches plain Markdown links to local generated files, such as
`[slides.pdf](/workspace/output/slides.pdf)`, and uploads them in the same
Discord reply. Agents should still prefer the JSON protocol because it avoids
leaking unusable local paths into chat.

The gateway validates generated files before sending them. By default only
`/tmp/studyos-artifacts`, `/workspaces`, and legacy `/workspace` are uploadable
roots. This keeps auth volumes and logs from being posted accidentally.

Discord itself stays conversational: normal replies should be one to three
short sentences. If the final response contains fenced code, a Markdown
document, more than 12 lines, or more than 900 characters, the gateway writes
the full response to a Markdown artifact and sends only a short caption plus
the attachment.

For simple architecture or workflow diagrams, the agent image includes
Graphviz and the helper CLI:

```bash
studyos-render-diagram --input /tmp/studyos-artifacts/flow.dot --output /tmp/studyos-artifacts/flow.png
```

The proactive Discord monitor is disabled by default. When enabled, it only
considers private `group-*` channels and their threads. A deterministic gate
requires a settled, unanswered technical blocker; recent bot activity, ordinary
conversation, summaries, cheerleading, and generic next-step suggestions are
silenced before the agent can post. The agent must then return a strict JSON
decision, and any post is limited to 500 characters and four lines with no code
block. Keep `DISCORD_PROACTIVE_DRY_RUN=true` until behavior is trusted in a real
course server.

## Channel Sessions

When `AGENT_CHANNEL_SESSIONS_ENABLED=true` and `AGENT_COMMAND` is a Codex
`exec` command, the gateway launches one persistent `codex app-server` process
over stdio and keeps one persisted Codex thread per Discord channel. The thread
ID is stored in:

```text
$CODEX_HOME/gateway/discord-channel-sessions.json
```

The first turn uses `thread/start`; a stored thread uses `thread/resume`. While
a turn remains active, later mentions in the same channel run:

```text
turn/steer(threadId, expectedTurnId, input)
```

This keeps the follow-up in the active model turn and prevents a second response
handler. The initiating user can also steer with an unmentioned message while that
exact channel task remains active. An unmentioned message cannot wake an idle
session, start a later turn, or steer another user's task. Stop requests use
`turn/interrupt`. Different channels can run in parallel. GitHub poller and
webhook-triggered runs continue to use the configured one-shot command path.

The gateway also creates one editable Discord Components V2 progress card for each
active turn. It mirrors structured `turn/plan/updated` steps as a bounded checklist
and renders only allowlisted lifecycle and high-level activity data; raw commands,
outputs, diffs, tool arguments, web queries, and reasoning are never copied into
Discord. Its requester-only **Stop task** button arrives through the bot's existing
outbound Discord Gateway connection and does not require an inbound HTTP port. After
the final reply succeeds, the progress card is deleted. On failure, that same card
becomes the visible error state.

For Discord-originated Codex sessions, `AGENT_DISCORD_WORKTREE_ROOT` gives each
originating channel or thread a persistent working root such as
`/workspaces/.studyos-discord-worktrees/<channel-id>`. When a request mentions
exactly one `Tue-StudyOS/<repo-name>` repository, the gateway clones or reuses
the canonical checkout under `/workspaces/Tue-StudyOS/<repo-name>`, creates or
reuses a detached git worktree under that channel/thread root, rewrites Codex
`--cd` to the worktree, and starts the initial Codex session there.

If the target repository is not yet clear, Codex starts in the channel/thread
root and is prompted to create repo-specific worktrees there before editing
repository files. Prefer task- or channel-specific branch names, verify worktree
directories are ignored, and keep commits grouped logically. If the active
Codex runtime exposes subagents or delegation tools, the prompt tells Codex to
use them for independent subtasks and review; otherwise it should continue in
the current session and state that subagents are unavailable.

## Git Identity

Gateway startup configures the agent runtime's global Git author as:

```text
StudyOS Org <agents@studyos.invalid>
```

That identity controls commit author/committer metadata for repositories that
do not override Git identity locally. GitHub pull requests, issue comments, and
review comments are still authored by the authenticated GitHub account or app
selected through `GH_CONFIG_DIR`; Git config cannot change that actor.

The agent image carries static StudyOS automations under `codex/automations/`.
Container startup copies them into `$CODEX_HOME/automations/` without
overwriting existing edits. This uses the path Codex app automation runners
expect. A seeded automation can be enabled by changing its TOML `status` to
`ACTIVE`; automations that already ship as `ACTIVE` run when the mounted Codex
home is managed by a Codex automation runner.

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
`DISCORD_PR_CHANNEL_ID` is optional for this path; without it, the webhook only
invokes the agent and the agent should use GitHub as the primary response
surface. With a channel configured, the gateway also mirrors webhook
notifications and agent summaries into Discord.

The generated prompts ask for PR review summaries, issue refinement questions, duplicate detection, and next steps. They explicitly tell the agent not to merge pull requests. GitHub write access should still be controlled by:

- `gh auth login` scopes
- branch protection
- human-only merge policy

Implementation is human-gated. The gateway can help refine an issue until it is ready, but branch or PR creation should start only after a student explicitly asks the agent to implement a specific issue in Discord or in a GitHub issue comment.

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
threads, stale PRs, and implementation candidates. Comment only when
there is a useful update. Do not create branches or PRs from unattended
automation; wait until a human explicitly asks the agent to implement a
specific issue. Never merge pull requests.
```

For StudyOS, prefer this simpler Codex automation when it is enough. Webhooks are only needed for low-latency updates into Discord or immediate issue-refinement prompts. The bot-side poller remains useful when the result should post into Discord or reuse the same agent command configured for Discord mentions.

## Authentication Model

The deployable image should include the bot and any CLIs you need. It should not include credentials.

Use runtime injection instead:

- `DISCORD_TOKEN` as an environment variable or secret
- GitHub CLI auth in the `gh-auth` Docker volume, or `GITHUB_TOKEN` as a fallback
- StudyOS organization GitHub CLI auth in the `gh-studyos-org-auth` Docker
  volume for repositories under `Tue-StudyOS/*`
- External-public GitHub CLI auth in the `gh-public-auth` Docker volume when
  the agent needs to fork and open PRs against public repositories outside the
  default fine-grained token's repository selection
- `CODEX_HOME` in the `codex-auth` Docker volume, or mounted from a host auth directory
- Claude Code auth mounted or configured according to its deployment mode
- SSH deploy keys mounted read-only if the agent needs Git over SSH

The gateway sets `GH_CONFIG_DIR=/auth/gh` for normal scoped repository work and
`GH_STUDYOS_ORG_CONFIG_DIR=/auth/gh-studyos-org` for `Tue-StudyOS/*`
repositories. It also sets `GH_PUBLIC_CONFIG_DIR=/auth/gh-public` for public
open-source contribution flows. Agents should switch profiles per command
instead of copying tokens or embedding credentials in remotes.

## Interactive Container Login

The default agent compose file creates persistent `gh-auth` and `codex-auth` volumes. Log in once:

```bash
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway gh auth login
docker compose -f docker-compose.agent.yml exec studyos-agent-gateway codex login
```

After that, Discord mentions, GitHub webhooks, and the GitHub poller can invoke the authenticated tools without embedding tokens in the image.
