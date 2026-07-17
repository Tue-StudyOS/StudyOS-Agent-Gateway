# GitHub-To-Discord Native Intake Design

**Date:** 2026-07-17 | **Status:** Approved direction | **Scope:** Passive PR/issue intake

This is a focused extension of
`2026-07-17-discord-native-task-control-design.md`. That design remains authoritative for
the shared task lifecycle, cards, ownership, recovery, and Discord delivery; this document
governs GitHub webhook intake and GitHub-specific task capabilities.

## Summary

GitHub pull requests and issues are passively mirrored into the configured `#pr-review`
channel as native Discord cards. A webhook can create or update a card, but it cannot
invoke the agent, start work, or write to GitHub.

An authorized user starts work explicitly by clicking Review, Security review,
Vulnerability scan, or Work on this, or by mentioning/replying to the bot with an
instruction in the mirrored item's context. Results stay in Discord by default. External
writes are allowed only when the user's instruction names that exact operation; merge is
never authorized.

## Goals

- Make PRs and issues visible where the StudyOS team already coordinates reviews.
- Keep webhook receipt passive, deduplicated, and free of automatic agent side effects.
- Expose four native actions with narrow, obvious capability boundaries.
- Reuse the durable Discord task lifecycle instead of creating a second job system.
- Route execution to the exact validated repository and retain item context across restart.
- Keep results conversational in Discord without silently posting GitHub comments.

## Non-Goals

- Automatic review, refinement, implementation, comments, labels, assignments, pushes,
  pull requests, issue closure, or any other agent action from a webhook.
- Active probing, exploitation, port scanning, or requests against deployed targets.
- A GitHub dashboard, repository picker, role-management UI, or one command per operation.
- Bot-controlled merge. Merge remains human-only even if an instruction requests it.

## Native Discord Experience

### Passive mirror cards

Verified `pull_request`, `issues`, and relevant `issue_comment` webhooks upsert one
Components V2 card per logical `repository/item-kind/item-number` in
`DISCORD_PR_CHANNEL_ID`, intended to be `#pr-review`. The existing setting now names the
shared PR-and-issue intake channel.

The card contains bounded repository, number, title, state, author, labels or branch
context when available, and a GitHub link. It does not copy full PR/issue bodies, comments,
reviews, diffs, or logs. Opened, edited, reopened, ready-for-review, synchronized, labeled,
unlabeled, closed, and comment activity updates the existing card when relevant. Comment
events may refresh bounded activity metadata but never copy the comment body. A missing
prior card may be recreated without starting agent work.

`X-GitHub-Delivery` deduplicates webhook retries. The logical item key prevents new cards
for later events on the same item. Card edits preserve the associated Discord thread and
active task link.

### Explicit intake actions

Active cards expose exactly four actions:

| Action | User input | Capability |
|---|---|---|
| Review | None | Read-only correctness, regression, tests, and maintainability review |
| Security review | None | Read-only auth, secrets, permissions, privacy, trust, and abuse review |
| Vulnerability scan | None | Read-only local SAST and dependency checks against pinned objects already present in the canonical checkout |
| Work on this | Required modal | Implement the submitted instruction in the isolated worktree |

The first three actions use fixed bounded prompts and start immediately after authorization.
Vulnerability scan cannot probe live hosts or services. Work on this opens a required
instruction modal; an empty submission does not create a task.

The first accepted action creates or reuses one public item thread beneath the mirror
card. The thread becomes the execution channel, session key, worktree isolation key, and
default result destination. A concurrent action links to the canonical active task instead
of starting duplicate work. A new action is permitted after that task is terminal.

### Mention and reply intake

An explicit bot mention in item context, or a reply to the bot's mirror card or item-thread
message, resolves the same typed GitHub item context. The user's text is the instruction
and enters through `DiscordTaskService`; webhook publication remains passive.

Ordinary conversation in `#pr-review` does not start work merely because it is near a
mirror card. The message must explicitly address or reply to the bot through the existing
mention rules.

## Authorization And Idempotency

- The actor must be a current guild member who can view the intake channel. If the item
  thread already exists, they must also view it; after first creation, access is verified
  before task start. The clicking actor owns the resulting task.
- The bot must be able to view/send in the destination and create, view, and send in the
  item thread. Missing permissions fail ephemerally with no parent-channel execution
  fallback.
- Component custom IDs contain only the action and an opaque 32-hex mirror ID. They never
  contain a repository, item title, URL, user ID, or token.
- The handler claims each Discord interaction ID and compare-and-swaps the mirror revision
  before creating a thread or task. Retries and double clicks return the existing result.
- The mirror's active task ID and the task store's one-active-task-per-thread rule provide
  a second idempotency boundary across processes and restarts.
- Webhook-delivery and interaction idempotency are independent; receiving an update cannot
  reserve or start an action.

## Architecture

### Typed webhook and mirror state

Webhook parsing produces `GitHubMirrorEvent`, never an agent prompt. The passive publisher
renders or edits the card and is not allowed to call `AgentGateway` or
`DiscordTaskService`.

`GitHubMirrorStore` writes `$CODEX_HOME/gateway/github-mirrors.json` atomically with mode
`0600`. Each record contains an opaque mirror ID, bounded recent delivery IDs and
interaction claims, repository/item identity and URL, bounded display metadata,
guild/channel/card/thread IDs, revision, pending action reservation, and active task ID.
It stores no webhook secret, access token, full body/comment, agent prompt/output,
attachment, or raw exception.

The controller preallocates a task ID and persists it with the pending action claim before
side effects. Startup reconciliation links a claim whose task exists, or marks an orphaned
claim failed and releases the item for a new interaction. Bounded handled claims make the
same interaction idempotent without retaining modal text or actor identity.

### Task bridge and execution context

`GitHubMirrorActionItem` resolves the current record after restart, authorizes and claims
the interaction, creates/reuses the item thread, and passes a typed request to the shared
task service. `GitHubTaskContext` carries mirror ID, repository full name, item kind,
number, URL, and optional validated PR base/head commit IDs. The task record persists only
the opaque mirror reference plus intent.
Retry and Continue re-resolve that reference through the mirror store; a missing record is
an explicit safe failure, never a prompt-parsing fallback.

The four values of `DiscordTaskIntent` are `review`, `security_review`,
`vulnerability_scan`, and `implementation`; existing tasks use `general`. Validated
`repository_full_name` is carried through `AgentExecutionContext` so worktree routing uses
that exact repository rather than extracting a target from untrusted prompt text.

Mention/reply resolution looks up a referenced mirror card or item thread in the same
store, then uses the same typed bridge. Dynamic item registration and mirror reconciliation
run once at bot startup alongside ordinary task-card reconciliation.

### Capability and write boundary

All GitHub titles, bodies, comments, diffs, and repository content are delimited as
untrusted data, never instructions. Prompt construction is owned by the intent bridge:

- Review and Security review forbid repository mutation and every external write.
- Vulnerability scan permits safe local static/dependency commands against pinned objects
  already present in the canonical checkout, but forbids checkout mutation, cloning,
  fetching, repository mutation, active probing, exploitation, and every external write.
- Work on this permits isolated worktree changes requested by the modal instruction.
- A modal or mention/reply may authorize a specific comment, review, label, assignment,
  push, PR creation, close, or other external write only when the user's text explicitly
  requests that exact operation. Intent, item state, and proximity never imply permission.
- Merge is rejected for every intent and instruction.

Every task reports its analysis or implementation result in the item thread. No action
automatically posts that result as a GitHub review or comment.

## Failure, Privacy, And Operations

- Missing or inaccessible `DISCORD_PR_CHANNEL_ID`, card/thread delivery failure, store
  corruption, stale components, and unauthorized actions are explicit safe errors.
- There is no alternate-channel, one-shot-agent, automatic-write, or mock-data fallback.
- Allowed mentions remain disabled for cards and results. Logs use mirror/task IDs and
  exclude titles, instructions, personal IDs, URLs with secrets, and raw provider errors.
- Closing or deleting a GitHub item updates card state but never interrupts or starts a
  task. Task control remains an explicit Discord action.
- Deployment must remove the obsolete `AGENT_AUTO_REVIEW_ENABLED` control so stale
  environment configuration cannot restore webhook-driven work.

## Verification

Focused tests cover:

- PR and issue event rendering, logical-item upsert, delivery deduplication, card edits,
  missing-card recreation, and explicit channel failure;
- proof that every webhook action has no agent prompt and never calls agent/task/write
  boundaries, including with a stale auto-review environment value;
- restart-safe opaque components, actor/bot authorization, interaction claim and CAS,
  item-thread create/reuse, active-task linking, and duplicate-click suppression;
- exact prompts and capabilities for all four actions, Work on this modal validation,
  safe scan tooling, and explicit-write/merge denial;
- typed repository routing, opaque task persistence, mention/reply context resolution,
  untrusted-content delimiting, and Discord-only default delivery.

The deployment smoke sends one safe PR event and one safe issue event, repeats a delivery,
and exercises all four actions in the StudyOS test guild. It verifies one card/item, one
thread/item, no webhook-started task, Discord results, and no unrequested GitHub write.

## Acceptance Criteria

1. PR and issue webhooks upsert native cards in configured `#pr-review` without invoking
   the agent or writing to GitHub.
2. Authorized Review, Security review, Vulnerability scan, and Work on this interactions
   create exactly one typed task in the item thread with the documented capability.
3. A bot mention/reply in item context creates an instructed task through the same service.
4. Duplicate deliveries/interactions remain idempotent across restart.
5. Results remain in Discord by default; external writes require an exact explicit user
   instruction, Vulnerability scan never actively probes, and no path can merge.
