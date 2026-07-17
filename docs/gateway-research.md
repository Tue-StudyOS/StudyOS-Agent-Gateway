# Gateway Research Notes

StudyOS Agent Gateway follows the same broad shape as OpenClaw: one gateway process receives messages from external surfaces, resolves routing and permissions, then forwards work to an agent runtime.

## OpenClaw Patterns Worth Reusing

OpenClaw's gateway model is useful for StudyOS because it separates channel plumbing from agent execution.

Useful patterns:

- Pairing or allowlists for unknown users before processing messages.
- Guild/channel allowlists instead of a single global bot permission.
- Mention-gated behavior in group channels.
- Isolated sessions per Discord channel, user, issue, PR, or bound agent.
- Role-based routing to different agents.
- Interactive Discord components for approval buttons and forms.
- A control-plane mindset: the gateway owns routing, auth, and delivery; the agent owns reasoning and code work.

The immediate version of this repo keeps a simpler shape: mention-triggered
Discord agent work and optional passive GitHub webhook mirrors. There is no
autonomous GitHub poller.

## Codex Surfaces

Codex has several useful integration surfaces.

### `codex exec`

Good default for jobs explicitly triggered from Discord:

```bash
codex exec --json --dangerously-bypass-approvals-and-sandbox --cd /workspace -
```

The prompt is passed on stdin by this bot. For automation output, Codex supports JSON Lines:

```bash
codex exec --json --cd /workspace "summarize open pull requests"
```

JSONL events include thread/turn lifecycle events and item events for messages, command executions, file changes, MCP calls, web searches, and plan updates. This is the best next hook for posting live progress back into Discord.

### Session Continuation

Codex can continue a previous non-interactive session:

```bash
codex exec resume <SESSION_ID> "continue with the next issue"
```

For this bot, store a mapping like:

```text
discord channel id -> codex session id
github PR number -> codex session id
issue number -> codex session id
```

That prevents unrelated Discord channels or PRs from interrupting each other.

### Hooks

Codex hooks are useful for guardrails and status, not for receiving Discord messages.

Useful hook events:

- `SessionStart`: inject StudyOS course/repo context.
- `UserPromptSubmit`: record or normalize incoming prompts.
- `PreToolUse`: block unsafe shell commands or edits.
- `PermissionRequest`: decide approval requests from policy.
- `PostToolUse`: inspect command output or add context.
- `Stop`: optionally continue or clean up at the end of a turn.

Hooks receive JSON on stdin and can return JSON on stdout. For example, a `PreToolUse` hook can deny destructive shell commands. These hooks should be added as a defense-in-depth layer around the agent runtime.

### Codex Automations

For scheduled GitHub reporting, a separate Codex automation runner can own the
cron cadence. The gateway does not start such work.

Use this for detached jobs:

- inspect open issues and PRs every 15 or 30 minutes
- unify duplicate issue threads
- summarize stale or blocked PRs
- identify small implementation candidates
- propose PR plans for bounded fixes
- prepare a report for a human without changing GitHub

Seeded automations ship paused and should remain read/report-only if activated.
For StudyOS, a student must explicitly ask the agent to act before it comments,
changes metadata, creates a branch, or opens a pull request.

### App Server

The Discord channel-session path now uses `codex app-server` instead of spawning
`codex exec` for every turn.

Why it matters:

- JSON-RPC thread and turn lifecycle.
- Stdio transport for local trusted integration.
- WebSocket transport for localhost or SSH-forwarded clients.
- Server-sent approval requests for command execution and file changes.
- Bounded queues and overload signaling.
- Separate threads for Discord channels, PRs, issues, or scheduled jobs.

The configured `AGENT_COMMAND` remains the compatibility and policy surface;
the gateway derives the app-server launch and thread settings from it.

## Proposed Evolution

1. Current: persistent app-server threads for Discord channel sessions, including
   progress, steering, interruption, and concurrent channels.
2. Add durable job records for restart-visible status and recovery.
3. Surface app-server approval requests through explicit Discord controls.
4. Add Codex hooks for policy enforcement and validation, not UI transport.

## Safety Baseline

- Keep GitHub branch protection enabled.
- Keep Discord write commands role-restricted.
- Treat all Discord and GitHub comments as untrusted input.
- Prefer mention-gating in shared channels.
- Do not expose Codex app-server WebSocket on a public interface.
- Do not bake CLI auth into images; use Docker volumes or deployment secrets.
