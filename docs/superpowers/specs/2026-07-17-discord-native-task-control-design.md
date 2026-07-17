# Discord-Native Task Control Design

**Date:** 2026-07-17 | **Status:** Approved direction; awaiting written-spec review | **Scope:** Discord-native task control

## Summary

StudyOS keeps mention-first conversation while adding a native Discord control surface
around the same agent lifecycle. Slash/context actions improve discovery; one persistent
Components V2 card and a bounded task registry preserve control and restart-safe status.

Tasks stay in the invoking channel or thread unless `/study ask` explicitly requests a
dedicated thread. The gateway never silently changes execution mode or reruns work.

## Decision And Goals

The selected task-control foundation normalizes mentions, commands, context actions, and
buttons onto one service, then adds three commands. It is more durable than duplicating
prompt commands and less coupled than starting with course-specific GitHub workflows.

- Preserve `@StudyOS <request>` as the lowest-friction entry point.
- Add discoverable slash and message-context entry points.
- Give every task a stable ID, owner, lifecycle state, and canonical card.
- Reuse one execution path for messages and interactions.
- Support requester-controlled Add context, Stop, safe Retry, and Continue actions.
- Replace generic failures with a safe reason, recovery status, and useful next action.
- Recover task metadata and eligible component actions after restart.
- Keep new task and interaction modules focused and roughly below 300 lines.

## Non-Goals

- Direct commands to merge PRs, close issues, or perform other GitHub writes without a
  separate confirmation design. Removed direct GitHub commands stay removed.
- A command for every possible prompt, repository autocomplete, or a project dashboard.
- Multiple simultaneous turns in one Discord channel/thread or moving an active task.
- DMs, user-installed app contexts, or cross-server tasks.
- Codex app-server approval requests or `requestUserInput`; those need a separate
  fail-closed protocol and authorization design.
- The ChatGPT Apps SDK or an MCP-hosted ChatGPT UI. Here, “app server” means the OpenAI
  `codex app-server` process already used by the gateway.

## Discord Experience

### Mention flow

A mention starts as today. During a turn, only the requester may steer with ordinary or
mentioned follow-ups. Another user's mention links to the active task and suggests a new
thread; it never steers or stops the existing task.

### Slash commands

- `/study ask [prompt] [dedicated_thread]`
  - Missing `prompt` opens a modal with one required multiline task field.
  - `dedicated_thread` defaults to `false`. If true, the bot creates a public thread,
    posts the card there, and uses that thread ID for session/worktree routing.
  - Thread creation is accepted only from a guild text channel. Unsupported channel or
    permission states fail explicitly and never run in the parent channel.
- `/study tasks [scope] [state]`
  - `scope` is `mine` by default or `channel`; `state` is `all`, `active`, or `terminal`.
  - The ephemeral result lists at most ten recent visible tasks and links to their card
    or source message.
- `/study status <task>`
  - Autocomplete offers recent tasks visible to the caller.
  - The ephemeral result shows persisted state, the same safe failure/next-action detail
    as the card, and caller-authorized actions even if the card is unavailable.
  - Autocomplete is not authorization: manually supplied IDs undergo the same-guild
    and current channel-visibility checks.
  - The owner can confirm forgetting an inactive record. This removes local metadata,
    not Discord messages or the underlying Codex session.

Commands are guild-only. With `DISCORD_GUILD_ID`, declarations are copied and synced to
that development guild; otherwise they sync globally. Startup stops clearing the tree.
`DISCORD_GUILD_ID` remains a sync target, not a new allowlist. All intake and component
paths require normal guild membership and channel access and otherwise preserve the
deployment's current guild reach.

### Message context action

`Ask StudyOS about this` opens an instruction modal. The selected message, bounded
attachments, author, channel context, and jump URL become typed source context. The task
runs in that message's channel/thread without creating another thread.

### Canonical task card

Every task creates one persistent Components V2 card, updated in place with:

- short task ID, requester, relative start time, and state;
- bounded plan and safe high-level activity, never raw commands or reasoning;
- source/result links when available;
- controls valid for the public state; every callback separately authorizes its caller.

While running, the card offers **Stop task** and, only if the runtime supports steering,
**Add context**. Add context opens a modal and calls the existing steer path for the
exact active turn. `NO_ACTIVE_TURN` or `NOT_STEERABLE` produces an ephemeral explanation
and never claims the input was queued.

On success, normal result/artifact delivery remains and the card offers **View result**
and, only when the runtime can resume the session, **Continue**. Continue opens an
instruction modal only for the latest task in that channel/thread because the Codex
conversation is channel-keyed, not task-keyed. It creates a linked task/card on the same
session/worktree while preserving the old result. Older cards explain that a new task or
dedicated thread is required.

On failure, the card is rebuilt without the obsolete **Stop task** action. It shows a
specific safe category and consequence, for example:

- `Timed out after 30 minutes. Partial work and the agent session were kept.`
- `Codex app server disconnected. The session and worktree were kept.`
- `Agent process exited before returning a result.`
- `Agent finished, but Discord could not deliver the result.`

**Why it failed** returns requester-only detail: category, safe explanation, whether
partial work is preserved, whether retry is safe, and the task ID for log lookup. It
never exposes traceback, raw stderr, command output, prompts, tokens, or host paths.

**Retry** is shown only for a capability selected by `AgentGateway`:

- `continue_session` starts a new turn in the persisted session/worktree, instructing
  the agent to inspect existing work and finish without repeating completed work;
- `retry_delivery` reposts a bounded in-memory `PreparedDiscordReply` without the agent,
  but only after a definitive pre-send/non-delivery error;
- `none` hides Retry and explains the required corrective action.

Timeouts and recoverable app-server exits use `continue_session` only if the gateway
confirms a persisted session and no still-active turn. Configuration, invalid output,
unsafe one-shot, unknown non-resumable failures, and ambiguous Discord network outcomes
use `none`. A delivery failure must never rerun the agent because that could duplicate
commits, PRs, comments, or other effects; ambiguous delivery tells the user to check the
channel first.

## Architecture

### Typed intake and task service

Adapters construct a transport-neutral `DiscordTaskRequest` with source kind, guild,
execution channel/thread, actor and trigger-event IDs, optional source-message ID,
prompt, validated attachment paths/context, and optional thread request. Adapters
validate, acknowledge, and delegate; they never call `AgentGateway` directly.

`DiscordTaskService` owns `start`, `steer`, `stop`, `retry`, `continue_task`, lookup,
authorization, atomic state transitions, failure classification, retry selection, and
card coordination. `DiscordMentionCoordinator` becomes a thin adapter; commands,
context action, and dynamic components use the same service. Existing `AgentGateway`
ask/steer/interrupt, session store, attachment validation, worktree selection, and reply
preparation remain the execution boundaries.

Only a finalized `PreparedDiscordReply` is cached, with at most ten files and the existing
per-file size limit. Paths are revalidated as existing and inside allowed artifact roots
before Retry; generated files remain owned by the cache and are deleted on success,
downgrade, or shutdown. Partial or ambiguous sends are never cached for Retry. New
focused modules hold task values/transitions, store, authorization, service, commands,
and cards without duplicating invocation or reply preparation.

### Codex app-server contract

The persistent Codex app-server path is the primary runtime. When channel sessions are
enabled and `AGENT_COMMAND` resolves to Codex:

- startup completes app-server `initialize` before Discord accepts tasks; initial
  initialization/protocol mismatch is readiness-fatal, so the bot does not log in;
- start uses `thread/start` or `thread/resume`, followed by `turn/start`;
- Add context uses `turn/steer` with the exact active turn ID;
- Stop uses `turn/interrupt`;
- Retry/Continue starts another turn on the same Codex thread and channel/thread
  worktree, never a silent replacement session;
- existing lifecycle, plan, command, file, tool, commentary, completion, and token
  notifications continue driving the public progress summary and usage records.

A required execution channel key—not optional `source_message_id`—selects app-server and
worktree routing. `source_message_id` is context only; slash, Retry, and Continue turns
therefore cannot fall through to one-shot execution. Reply preparation uses the task ID
as its delivery-file key when no source message exists.

A process exit atomically marks the runtime unavailable, unsubscribes the dead client,
clears its started state, and fails every concurrent active turn as
`runtime_disconnected`. The next start/Retry enters one single-flight recovery: close the
stale transport, create and initialize a new client, resubscribe, then start or resume the
stored channel thread. A disconnected Retry starts no new attempt until reconnect and
resume succeed; it first claims `recovering`, returns to failed if recovery fails, and
never runs a one-shot command.

A dedicated Discord thread ID becomes the existing app-server session/worktree key.
Other runners support start and final delivery, but controls requiring steer/resume are
omitted. Initialization or protocol mismatch becomes `runtime_incompatible`; it never
falls back to one-shot execution. After login this is a typed task failure; initial
startup incompatibility remains readiness-fatal as defined above.

`Dockerfile.agent`'s pinned Codex version is the contract. Implementation generates the
protocol schema from that exact binary and runs a real stdio smoke in the built image.
Host CLI behavior from a different version is not proof of compatibility.

### Durable task model

Each record stores a UUID plus short display prefix, owner/guild/origin/execution IDs,
source/card/result message IDs, neutral source label, optional continuation links,
timestamps, attempt, state, optional interruption cause, and—on failure—typed category,
safe summary, and retry mode. It stores no prompt, output, raw error, credential, or
attachment content.

States and allowed transitions are:

- `failed | timed_out | interrupted -> recovering` via resumable Retry;
- `recovering -> starting | failed | stopping`;
- `starting -> running | failed | stopping`;
- `running -> delivering | failed | timed_out | stopping | interrupted`;
- `stopping -> stopped | failed | interrupted`;
- `delivering -> completed | delivery_failed`;
- `delivery_failed -> delivering` via in-memory delivery Retry;
- latest-channel Continue atomically links a new `starting` child task while leaving the
  completed parent unchanged.
- startup reconciliation allows `recovering | starting | running | stopping -> interrupted` and
  `delivering -> delivery_failed` without invoking the agent.

Each callback claims its Discord interaction ID in a bounded deduplication set. A
state-changing action then performs an atomic compare-and-set; a Retry increments
`attempt`, while Continue atomically links exactly one child. Locks cover state/store
work only and are released before Discord or agent I/O, so duplicate clicks cannot start
the same operation twice or serialize other channels.

Stop, timeout, runtime exit, and restart atomically claim `user_stop`, `timeout`,
`runtime_exit`, or `gateway_restart` before interrupt/cleanup. A previously recorded
`delivering`/`completed` result wins; otherwise the first cause claim wins and later
signals cannot rewrite it.

The store follows the existing JSON pattern at
`$CODEX_HOME/gateway/discord-tasks.json`, writes by atomic replace with owner-only mode,
and retains inactive records for 30 days up to 500, never pruning active records.

On startup the reconciliation transitions above are applied. `delivering` becomes
`delivery_failed` with retry mode `none`, and existing delivery Retry also becomes `none`,
because the cache is gone and delivery cannot be proven. Every affected reachable card
is fetched and rerendered; a deleted/inaccessible card leaves the store authoritative and
rendering fails best-effort. Dynamic handlers resolve stored IDs, so only capabilities
surviving restart remain actionable.

## Concurrency, Authorization, And Privacy

- One active task per execution channel/thread preserves runtime/worktree isolation.
- A dedicated thread gets its own channel-keyed session and worktree. Busy starts link
  to the active card.
- Owner checks apply equally to text follow-ups and component actions. A member with
  `manage_messages` may Stop for moderation but cannot steer, retry, or continue.
- Every lookup/action validates the current guild and the caller's current `view_channel`
  access to the execution/source channel, including owner IDs after access is revoked.
- Public cards/results follow Discord channel visibility. Task lists are ephemeral:
  `mine` is owner-only and `channel` includes only visible current-channel records.
- Allowed mentions stay disabled for generated cards and interaction responses.
- Every intake path accepts at most ten attachments of at most 8 MB each. Downloaded
  inputs are removed in `finally`; validation failure starts no task.

## Failure Handling

- I/O interactions defer within Discord's response window. Interaction validation and
  authorization failures are ephemeral; message failures are concise replies.
- Boundary errors map to `timeout`, `runtime_disconnected`, `agent_process_failed`,
  `invalid_agent_output`, `runtime_incompatible`, `configuration`,
  `workspace_or_attachment`, `discord_delivery`, or `internal`. Unknown exceptions use
  `internal`; raw exception strings never enter Discord.
- Every failure says what failed, whether work was kept, and what to do. Retry is present
  only when safe and is idempotent across duplicate clicks.
- Missing configuration, command-sync/thread permission failure, and store corruption
  are explicit errors with no fallback.
- Missing card delivery does not fail agent execution; final output still uses the
  normal reply path. Failed final delivery stays non-completed and retains the card.
- Unknown, stale, expired, or handled component actions explain themselves ephemerally
  and never invoke the agent twice.
- Logs include task ID and operation but exclude prompts, tokens, raw errors, and personal
  message content.

## Verification

Focused tests cover all intake adapters; command sync, autocomplete, modals, deferral,
and thread creation; store round trips, retention, corruption, and restart recovery;
all transitions, compare-and-set races, attempts, and authorization; channel isolation;
dynamic component routing; Add context, Stop, Retry, latest Continue, and stale cards;
safe failure rendering and duplicate Retry suppression; delivery-only Retry; restart
downgrade; forgetting; attachment limits/cleanup; and unchanged mentions, artifacts,
long replies, progress, and worktree routing. They also prove a source-less slash/Continue
uses the channel app-server/worktree and that cross-guild/private-channel IDs are denied.

Readiness requires `ruff check .`, `pyright`, and `pytest`, plus:

- a real Discord smoke for guild sync, slash/context modal starts, owner rejection,
  optional thread creation, useful failure details, Retry, and restart-visible controls;
- a real authenticated smoke using the pinned `codex app-server` for initialize,
  source-less start, progress, steer, interrupt, completion, same-thread Retry/Continue,
  and forced child-process death followed by single-flight reinitialize/resume.

If a safe test guild or authenticated runtime is unavailable, report that verification
as missing; fake-protocol tests are not a substitute.

## Acceptance Criteria

1. Mentions, `/study ask`, and `Ask StudyOS about this` use one task service.
2. `/study tasks` and `/study status` expose durable, authorized task state.
3. Public cards show state-valid controls; callbacks authorize and are idempotent.
4. Requested dedicated threads isolate session/worktree or fail without fallback.
5. Another user cannot steer, text-stop, retry, or continue the owner's task.
6. Failed cards explain the safe reason, remove Stop, and offer Retry only when safe.
7. The pinned app-server passes the real lifecycle/control smoke above.
