# Task 6 Report: Durable Discord Task Coordination

## Result

- Added the shared `DiscordTaskService` with typed start, steering, Stop, Retry,
  Continue, Forget, status, list, control-resolution, startup reconciliation,
  and shutdown boundaries.
- Split action coordination, runtime execution, failure recording, delivery,
  queries, reconciliation, runner ownership, persistence helpers, and store
  mutations into focused modules. Every changed production and test file is at
  most 298 lines.
- Kept Discord presentation record-driven. The service exposes fresh async
  control resolution for Task 7, including steering, resumability, and exact
  latest-created/unlinked/completed Continue eligibility.
- Push/deploy was not performed.

## Red-Green Evidence

The request, delivery, recovery, durability, and store tests were written before
the production modules. The initial focused command failed during collection
with five expected `ModuleNotFoundError` errors for
`discord_task_request`, `discord_task_delivery`, and `discord_task_service`.

The first implementation then passed 23 focused tests. Additional regression
cases expanded the focused suite to 28 passing tests for shutdown cancellation,
bounded trigger dedupe, Stop-versus-timeout ordering, fresh controls,
timezone-correct task ordering, and continuation-link durability ambiguity.

## Contract Evidence

- `STARTING` is persisted before card or agent I/O. A returned card ID is CASed
  before agent execution; result IDs and continuation links are persisted before
  rerendering. Post-replace durability ambiguity is resolved by rereading the
  canonical record before any downstream side effect.
- The store remains the one-active-task-per-execution-channel arbiter. Separate
  channels progress while another channel is blocked in presentation or agent
  I/O; no service-wide async lock spans external I/O.
- Accepted staged input remains runner-owned through `finally`. Duplicate and
  rejected starts/continuations clean immediately; steering always cleans after
  its attempt. Shutdown cancels and awaits runners without claiming user Stop,
  closes delivery ownership, drains the cache, and retries deferred staging
  cleanup.
- Every result attempt follows `cache.put -> cache.consume -> pinned lease`,
  enters `DELIVERING` before network I/O, and persists `COMPLETED` only after a
  result message ID is saved. Success and ambiguous outcomes close the lease.
  Only typed definitive non-delivery restores the exact reply/lease for a
  delivery-only Retry; missing cache disables Retry without agent execution.
- Generic Retry accepts only persisted retryable terminal states, CASes
  `RECOVERING` with one attempt increment, reconnects before `STARTING`, reuses
  the task ID, and sends a fixed resume instruction that cannot replay the
  original prompt. Runner callbacks are identity-safe when the task ID is reused.
- Stop durably claims `USER_STOP` and `STOPPING` before interrupting. Timeout and
  runtime exit claim their terminal state/cause before capability I/O, and the
  first interruption cause remains immutable across recovery attempts.
- Continue atomically links a same-scope child only from the latest-created,
  completed, unlinked task in its execution channel. Forget atomically removes
  only inactive owner-controlled tasks, unlinks both graph neighbors with fresh
  revisions/timestamps, and discards cached output without deleting Discord or
  agent history.
- Startup reconciliation enriches `GATEWAY_RESTART` interruptions from fresh
  `AgentGateway.channel_capabilities`: only an idle persisted session receives
  `CONTINUE_SESSION`; all other cases retain a safe non-retryable explanation.

## Verification

- Focused Task 6 pytest: `28 passed`.
- Full pytest: `498 passed` (1,189 existing Python 3.14/pytest-asyncio
  deprecation warnings).
- Full Ruff: `All checks passed!`.
- Full strict Pyright with the shared root interpreter: `0 errors, 0 warnings`.
  Pyright prints the existing worktree-local `.venv` discovery notice before
  successfully using the explicit shared interpreter.
- Linked named-branch worktree detected at
  `.worktrees/discord-native-task-control`; it is preserved as requested.
