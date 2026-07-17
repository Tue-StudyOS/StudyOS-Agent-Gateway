# GitHub-To-Discord Native Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add passive, deduplicated PR/issue mirror cards to configured `#pr-review` and
bridge four explicit native actions into the durable Discord task lifecycle.

**Dependency:** Complete Tasks 1-4 in `2026-07-17-discord-native-core.md` and Tasks 5-9
in `2026-07-17-discord-native-surface.md` first. This plan contains Tasks 10-12. Release
verification continues with Tasks 13-16 in `2026-07-17-discord-native-release.md`.

**Architecture:** A passive typed webhook publisher owns card upserts. An atomic mirror
store maps opaque component IDs and Discord threads to validated GitHub context. A
persistent action controller authorizes explicit interactions and delegates typed intents
to the existing `DiscordTaskService`; webhook delivery has no execution path.

**Tech Stack:** Python 3.12, discord.py Components V2/DynamicItem, aiohttp, Pydantic
settings, atomic JSON stores, pytest/pytest-asyncio.

## Global Constraints

- Follow `2026-07-17-github-discord-intake-design.md` for product and safety boundaries.
- PR and issue webhooks only upsert cards. They never call `AgentGateway`, start tasks, or
  write to GitHub.
- Review, Security review, and Vulnerability scan are read-only/local-analysis intents.
  Vulnerability scan has no active probing. Work on this requires modal instructions.
- Fixed review actions run with `approvalPolicy=never`, a read-only sandbox, network
  disabled, web search/apps disabled, and no workspace preparation or GitHub credentials.
  They inspect only an already-present canonical checkout and pinned local base/head object;
  a missing object fails explicitly without clone/fetch/checkout mutation. Work on this
  runs workspace-write in its isolated worktree with network disabled.
- External writes require an exact explicit textual instruction; merge is always denied.
- Results stay in the item thread by default. No action automatically posts a GitHub
  comment or review.
- Use `DISCORD_PR_CHANNEL_ID` as the shared PR/issue destination. Missing or inaccessible
  configuration is an explicit error with no alternate-channel fallback.
- Persist only bounded mirror metadata and opaque task references; treat all GitHub and
  repository content as untrusted data.
- Use TDD and keep each new module roughly below 300 lines.

---

### Task 10: Passive Event Model, Store, And Card Upsert

**Files:**
- Create: `src/study_discord_agent/github_mirror_model.py`
- Create: `src/study_discord_agent/github_mirror_store.py`
- Create: `src/study_discord_agent/github_mirror_cards.py`
- Create: `src/study_discord_agent/github_mirror_publisher.py`
- Modify: `src/study_discord_agent/github_events.py`
- Modify: `src/study_discord_agent/web.py`
- Modify: `src/study_discord_agent/main.py`
- Modify: `src/study_discord_agent/discord_bot.py`
- Create: `tests/test_github_mirror_store.py`
- Create: `tests/test_github_mirror_cards.py`
- Create: `tests/test_github_mirror_publisher.py`
- Modify: `tests/test_github_events.py`
- Create: `tests/test_web.py`
- Modify: `tests/test_discord_bot.py`

`GitHubMirrorRecord` contains opaque ID, guild/channel/card/thread IDs, validated
repository and item identity/URL, optional validated PR base/head commit IDs, bounded
display metadata, delivery IDs and handled
interaction claims, pending action reservation with preallocated task ID, active task ID,
and revision. `GitHubMirrorStore` writes
`$CODEX_HOME/gateway/github-mirrors.json` atomically with mode `0600`.

- [ ] **Step 1: Write failing model/store tests** for strict validation, one record per
  repository/item, bounded delivery deduplication, atomic rollback, `0600`, missing-card
  recreation state, pending/handled claims, active-task CAS, corruption failure, and
  restart reload. Assert no secret, token, body/comment, prompt/result, raw error, modal
  text, or actor identity is persisted.
- [ ] **Step 2: Write failing passive publisher tests** for PR/issue opened, edited,
  reopened, ready-for-review, synchronize, label, and closed rendering, plus issue and PR
  `issue_comment` activity without copied bodies. Cover Components V2 bounds, logical-item
  upsert, retry deduplication, delivery-header endpoint behavior, typed queue wiring, and
  explicit missing/inaccessible-channel failure.
- [ ] **Step 3: Prove every supported webhook has no execution side effect**: any
  compatibility `agent_prompt` is `None`, and publication never calls `AgentGateway`,
  `DiscordTaskService`, or a GitHub write client.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_github_events.py tests/test_github_mirror_store.py tests/test_github_mirror_cards.py tests/test_github_mirror_publisher.py tests/test_web.py tests/test_discord_bot.py -q` and expect import/behavior failures.
- [ ] **Step 5: Implement typed events, store, renderer, publisher, endpoint queue, and
  passive bot consumer** using only `DISCORD_PR_CHANNEL_ID`; disable allowed mentions and
  log safe mirror IDs.
- [ ] **Step 6: Rerun focused tests** and expect PASS.
- [ ] **Step 7: Commit**

```bash
git add src/study_discord_agent/github_events.py src/study_discord_agent/web.py src/study_discord_agent/main.py src/study_discord_agent/discord_bot.py src/study_discord_agent/github_mirror_model.py src/study_discord_agent/github_mirror_store.py src/study_discord_agent/github_mirror_cards.py src/study_discord_agent/github_mirror_publisher.py tests/test_github_events.py tests/test_github_mirror_store.py tests/test_github_mirror_cards.py tests/test_github_mirror_publisher.py tests/test_web.py tests/test_discord_bot.py
git commit -m "feat(discord): mirror GitHub intake cards"
```

### Task 11: Explicit Actions, Task Bridge, And Write Boundary

**Files:**
- Create: `src/study_discord_agent/github_mirror_components.py`
- Create: `src/study_discord_agent/github_mirror_controller.py`
- Modify: `src/study_discord_agent/agent.py`
- Modify: `src/study_discord_agent/codex_app_server.py`
- Modify: `src/study_discord_agent/codex_app_server_runtime.py`
- Modify: `src/study_discord_agent/session_store.py`
- Modify: `src/study_discord_agent/discord_worktrees.py`
- Modify: `src/study_discord_agent/discord_task_model.py`
- Modify: `src/study_discord_agent/discord_task_request.py`
- Modify: `src/study_discord_agent/discord_task_store.py`
- Modify: `src/study_discord_agent/discord_task_service.py`
- Create: `tests/test_github_mirror_components.py`
- Create: `tests/test_github_mirror_controller.py`
- Modify: `tests/test_agent_sessions.py`
- Modify: `tests/test_codex_app_server.py`
- Modify: `tests/test_codex_app_server_runtime.py`
- Modify: `tests/test_discord_worktrees.py`
- Modify: `tests/test_discord_task_store.py`
- Modify: `tests/test_discord_task_service.py`

Add `general`, `review`, `security_review`, `vulnerability_scan`, and `implementation`
task intents plus optional opaque `source_reference_id`. `GitHubTaskContext` carries mirror
ID, validated repository, item kind/number, URL, and optional pinned PR base/head commit
IDs. `AgentExecutionContext` carries the
validated repository to worktree routing; prompt text never selects or overrides it.
Each persisted intent maps to an immutable execution-policy fingerprint. The policy is
rehydrated for Retry/Continue and sent on thread start/resume and every `turn/start`.
Restricted sessions fail closed if thread start/resume reports a different effective
policy; every turn sends the exact persisted policy because Codex 0.144.1 does not return
an effective-policy object from `turn/start`.

- [ ] **Step 1: Write failing task-model migration tests** for all intents, `general`
  defaults, and opaque references without copied GitHub content.
- [ ] **Step 2: Write failing controller/component tests** for opaque-ID restart
  resolution, current guild/view authorization, actor ownership, bot thread permissions,
  one public item thread, no parent fallback, interaction claim/CAS, active-task linking,
  duplicate suppression, crash recovery before/after task persistence, orphaned-claim
  release, and a new action after terminal state.
- [ ] **Step 3: Assert exact capabilities**: Review is read-only correctness/tests;
  Security review is read-only auth/secrets/permissions/privacy/abuse; Vulnerability scan
  is read-only local SAST/dependency work with no probing/exploitation/live requests; Work
  on this rejects an empty modal and uses its implementation instruction.
- [ ] **Step 4: Test the write boundary and routing**. Fixed actions never authorize
  external writes. Work on this only mutates its isolated worktree by default. A specific
  external operation appears only when explicit in user text; merge is always rejected.
  A validated `Tue-StudyOS/example` context routes to exactly that repo. Retry/Continue
  rehydrate the opaque reference after restart or fail safely without prompt parsing.
- [ ] **Step 5: Test hard runtime policy**. Review/Security/Vulnerability turns send
  `approvalPolicy=never` plus `readOnly/networkAccess=false`; they use an existing local
  canonical checkout and cannot clone/fetch/create a worktree. Implementation sends
  `workspaceWrite/networkAccess=false`. Web search, apps, dynamic tool forwarding, and
  unsafe app-server RPCs remain unavailable. Launch configuration disables apps, browser,
  computer-use, MCP/dynamic tools, and web capabilities process-wide rather than trusting
  a session fingerprint to hide them. A policy-class change starts a new thread;
  start/resume response-policy mismatch fails closed.
- [ ] **Step 6: Run** `.venv/bin/pytest tests/test_github_mirror_components.py tests/test_github_mirror_controller.py tests/test_agent_sessions.py tests/test_codex_app_server.py tests/test_codex_app_server_runtime.py tests/test_discord_worktrees.py tests/test_discord_task_store.py tests/test_discord_task_service.py -q` and expect failures.
- [ ] **Step 7: Implement the components, controller, task bridge, persistence migration,
  intent prompts, item-thread delivery, and validated worktree routing**.
- [ ] **Step 8: Rerun focused tests** and expect PASS.
- [ ] **Step 9: Commit**

```bash
git add src/study_discord_agent/agent.py src/study_discord_agent/codex_app_server.py src/study_discord_agent/codex_app_server_runtime.py src/study_discord_agent/session_store.py src/study_discord_agent/discord_worktrees.py src/study_discord_agent/discord_task_model.py src/study_discord_agent/discord_task_request.py src/study_discord_agent/discord_task_store.py src/study_discord_agent/discord_task_service.py src/study_discord_agent/github_mirror_components.py src/study_discord_agent/github_mirror_controller.py tests/test_agent_sessions.py tests/test_codex_app_server.py tests/test_codex_app_server_runtime.py tests/test_discord_worktrees.py tests/test_discord_task_store.py tests/test_discord_task_service.py tests/test_github_mirror_components.py tests/test_github_mirror_controller.py
git commit -m "feat(discord): add explicit GitHub task actions"
```

### Task 12: Mention Context, Bot Wiring, And Reconciliation

**Files:**
- Modify: `src/study_discord_agent/discord_mentions.py`
- Modify: `src/study_discord_agent/discord_bot.py`
- Modify: `tests/test_discord_mentions.py`
- Modify: `tests/test_discord_bot.py`

- [ ] **Step 1: Write failing mention/reply tests**. An explicit bot mention in item
  context or reply to the bot's mirror/thread message resolves the same typed context,
  uses user text as instruction, and enters through `DiscordTaskService`. Nearby
  unaddressed messages and webhook publication stay passive.
- [ ] **Step 2: Write failing bot tests** for both DynamicItem registrations, one post-ready
  reconciliation of task and mirror stores, card recreation, retained thread/task links,
  and no agent call during webhook publication.
- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_discord_mentions.py tests/test_discord_bot.py -q` and expect failures.
- [ ] **Step 4: Wire mention resolution, controller dependencies, persistent components,
  and mirror reconciliation once**. Keep authorization and execution in their owning
  services rather than Discord callbacks.
- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_discord_*.py tests/test_github_*.py -q`
  and expect PASS.
- [ ] **Step 6: Commit**

```bash
git add src/study_discord_agent/discord_mentions.py src/study_discord_agent/discord_bot.py tests/test_discord_mentions.py tests/test_discord_bot.py
git commit -m "feat(discord): wire GitHub intake interactions"
```

## Completion Gate

- Tasks 10-12 have separate reviewed commits and all focused tests pass.
- PR/issue/comment delivery only upserts one bounded card per item.
- All four authorized actions and mention/reply intake create at most one item-thread task.
- Results stay in Discord; no unrequested external write or active probe is possible, and
  merge is always rejected.
