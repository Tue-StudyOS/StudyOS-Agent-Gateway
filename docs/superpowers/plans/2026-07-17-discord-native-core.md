# Discord Native Task Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed, durable, restart-safe task and Codex app-server foundation shared by every Discord entry point.

**Architecture:** Separate optional source-message context from a required Discord execution key, then route all Discord turns through one durable task service. Keep state transitions, persistence, authorization, failure classification, and app-server connection recovery in focused modules with no Discord or agent I/O under state locks.

**Tech Stack:** Python 3.12, asyncio, frozen dataclasses and `StrEnum`, discord.py 2.6+, pytest/pytest-asyncio, Ruff, Pyright, Codex JSON-RPC app server.

## Global Constraints

- Keep production modules roughly below 300 lines and do not duplicate agent invocation or reply preparation.
- Persist no prompt, output, raw exception, credential, personal display text, or attachment path/content.
- Store task JSON at `$CODEX_HOME/gateway/discord-tasks.json`, mode `0600`, retaining inactive records for 30 days and at most 500.
- One active task is allowed per execution channel/thread; locks cover state/store work only.
- A Discord task configured for Codex app-server execution never falls back to one-shot execution.
- Initial app-server incompatibility is readiness-fatal; post-start disconnects are typed recoverable failures.
- The companion [GitHub intake plan](2026-07-17-github-discord-intake-plan.md) extends the
  completed task model through typed intent and opaque source-reference fields.
- Use TDD and the StudyOS commit identity; run the narrow test named in each task before committing.

---

### Task 1: Typed Agent Boundaries And Source-Less Persistent Routing

**Files:**
- Create: `src/study_discord_agent/agent_errors.py`
- Modify: `src/study_discord_agent/agent.py`
- Modify: `src/study_discord_agent/command_runner.py`
- Modify: `src/study_discord_agent/discord_reply_content.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_agent_sessions.py`
- Test: `tests/test_discord_reply_content.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class AgentExecutionContext:
    channel_id: int
    trigger_event_id: int

@dataclass(frozen=True)
class AgentChannelCapabilities:
    steering: bool
    resumable: bool
    persisted_session: bool
    active_turn: bool

class AgentTurnTimedOut(RuntimeError): ...
class AgentRuntimeDisconnected(RuntimeError): ...
class AgentRuntimeIncompatible(RuntimeError): ...
class AgentProcessFailed(RuntimeError): ...
class AgentInvalidOutput(RuntimeError): ...
class AgentConfigurationError(RuntimeError): ...
class AgentWorkspaceOrAttachmentError(RuntimeError): ...
```

- `AgentGateway.ask(..., execution: AgentExecutionContext | None = None)` uses `execution.channel_id` for app-server, worktree, session, and usage routing. `channel_id` and optional `source_message_id` remain request metadata only.
- `AgentGateway.steer(..., source_message_id: int | None)` accepts source-less modal input.
- `prepare_discord_reply(..., delivery_key: str)` validates the key and names generated files without requiring a message ID.

- [ ] **Step 1: Write failing tests**

```python
async def test_source_less_discord_execution_uses_app_server_and_worktree(...):
    reply = await agent.ask(
        "continue", user="student", channel_id=123, source_message_id=None,
        execution=AgentExecutionContext(channel_id=123, trigger_event_id=9001),
    )
    assert reply.session_id == "thread-1"
    assert fake_runtime.calls[0].channel_id == 123
    assert prepared_workspace.name == "123"

async def test_metadata_only_call_without_execution_remains_one_shot(...):
    await agent.ask("summarize", user="scheduled-runtime", channel_id=123)
    assert one_shot.calls == 1
    assert fake_runtime.calls == []

def test_reply_attachment_uses_task_delivery_key(tmp_path):
    prepared = prepare_discord_reply(LONG_TEXT, (), tmp_path, "a" * 32)
    assert prepared.generated_file.name == f"reply-{'a' * 32}.md"
```

- [ ] **Step 2: Run tests and verify the routing assumptions fail**

Run: `.venv/bin/pytest tests/test_agent.py tests/test_agent_sessions.py tests/test_discord_reply_content.py -q`

Expected: source-less execution takes the one-shot path and reply preparation rejects a string key.

- [ ] **Step 3: Implement the typed boundaries and execution context**

Use exact exception types above; wrap configuration, command exit, empty output, and worktree/attachment boundaries without putting raw details in their public type. Select the app server solely from `execution`, and raise a typed error if that configured path fails.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_agent.py tests/test_agent_sessions.py tests/test_discord_reply_content.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/agent_errors.py src/study_discord_agent/agent.py src/study_discord_agent/command_runner.py src/study_discord_agent/discord_reply_content.py tests/test_agent.py tests/test_agent_sessions.py tests/test_discord_reply_content.py
git commit -m "feat(runtime): route source-less Discord tasks persistently"
```

### Task 2: Single-Flight Codex App-Server Recovery

**Files:**
- Create: `src/study_discord_agent/codex_app_server_connection.py`
- Modify: `src/study_discord_agent/codex_app_server_runtime.py`
- Modify: `src/study_discord_agent/codex_app_server_transport.py`
- Modify: `src/study_discord_agent/agent.py`
- Test: `tests/test_codex_app_server_runtime.py`
- Test: `tests/test_agent_sessions.py`

**Interfaces:**
- Consumes: `AgentRuntimeDisconnected`, `AgentRuntimeIncompatible`, and `AgentExecutionContext` from Task 1.
- Produces:

```python
ClientFactory = Callable[[], CodexAppServerClient]

class CodexAppServerConnection:
    async def start(self) -> CodexAppServerClient: ...
    async def invalidate(self, generation: int, error: BaseException) -> None: ...
    async def close(self) -> None: ...

class CodexAppServerRuntime:
    async def has_active_turn(self, channel_id: int) -> bool: ...
    def has_persisted_session(self, channel_id: int) -> bool: ...
```

The connection owns a client factory, generation-aware subscription, one lifecycle lock, stale-client retirement, and exactly one recovery task. Runtime exit fails all active turns as `AgentRuntimeDisconnected`; protocol/initialize mismatch raises `AgentRuntimeIncompatible`.

- [ ] **Step 1: Write failing recovery tests**

```python
async def test_exit_fails_all_turns_and_concurrent_retry_uses_one_client(...):
    first = create_task(runtime.run(channel_id=1, prompt="one", cwd=tmp_path))
    second = create_task(runtime.run(channel_id=2, prompt="two", cwd=tmp_path))
    await clients[0].emit_exit()
    with raises(AgentRuntimeDisconnected): await first
    with raises(AgentRuntimeDisconnected): await second
    resumed = await gather(
        runtime.run(channel_id=1, prompt="retry", cwd=tmp_path),
        runtime.run(channel_id=2, prompt="retry", cwd=tmp_path),
    )
    assert factory.calls == 2
    assert clients[1].resumed_threads == ["thread-1", "thread-2"]

async def test_failed_recovery_never_starts_one_shot_or_new_thread(...):
    with raises(AgentRuntimeDisconnected): await runtime.run(...)
    assert replacement.started_turns == []
    assert replacement.started_threads == []
```

- [ ] **Step 2: Run tests and verify recovery fails**

Run: `.venv/bin/pytest tests/test_codex_app_server_runtime.py tests/test_agent_sessions.py -q`

Expected: the dead runtime remains marked started or raises generic `RuntimeError`.

- [ ] **Step 3: Implement generation-safe recovery**

Do not close a client from its notification-dispatch callback. Mark that generation stale, fail active futures, and let the single-flight `start()` close/recreate/initialize/resubscribe. Resume a stored thread before `turn/start`; propagate resume failure and never call `thread/start` for that retry.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_codex_app_server_runtime.py tests/test_agent_sessions.py -q`

Expected: PASS, including timeout interrupt-grace behavior.

- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/codex_app_server_connection.py src/study_discord_agent/codex_app_server_runtime.py src/study_discord_agent/codex_app_server_transport.py src/study_discord_agent/agent.py tests/test_codex_app_server_runtime.py tests/test_agent_sessions.py
git commit -m "feat(runtime): recover Codex app-server sessions"
```

### Task 3: Durable Task Model And Atomic Store

**Files:**
- Create: `src/study_discord_agent/discord_task_model.py`
- Create: `src/study_discord_agent/discord_task_store.py`
- Create: `tests/test_discord_task_model.py`
- Create: `tests/test_discord_task_store.py`

**Interfaces:**

```python
class DiscordTaskState(StrEnum):
    STARTING = "starting"; RECOVERING = "recovering"; RUNNING = "running"
    STOPPING = "stopping"; DELIVERING = "delivering"; COMPLETED = "completed"
    DELIVERY_FAILED = "delivery_failed"; FAILED = "failed"; TIMED_OUT = "timed_out"
    STOPPED = "stopped"; INTERRUPTED = "interrupted"

@dataclass(frozen=True)
class DiscordTaskRecord:
    task_id: str
    revision: int
    owner_id: int
    guild_id: int
    origin_channel_id: int
    execution_channel_id: int
    trigger_event_id: int
    source_message_id: int | None
    card_message_id: int | None
    result_message_id: int | None
    source_kind: DiscordTaskSourceKind
    source_label: str
    created_at: str
    updated_at: str
    attempt: int
    state: DiscordTaskState
    failure: DiscordTaskFailure | None = None
    interruption_cause: DiscordTaskInterruptionCause | None = None
    continued_from_task_id: str | None = None
    continued_to_task_id: str | None = None

class DiscordTaskStore:
    def create(self, record: DiscordTaskRecord) -> None: ...
    def compare_and_set(self, task_id: str, expected_revision: int, update: Callable[[DiscordTaskRecord], DiscordTaskRecord]) -> DiscordTaskRecord: ...
    def link_child(self, parent_id: str, expected_revision: int, child: DiscordTaskRecord) -> tuple[DiscordTaskRecord, DiscordTaskRecord]: ...
    def reconcile_startup(self, now: datetime) -> tuple[DiscordTaskRecord, ...]: ...
```

- [ ] **Step 1: Write model/store tests** covering every allowed/invalid transition, first interruption-cause wins, strict schema corruption, failed-write rollback, `0600`, 30-day/500 retention, active preservation, parent-child transaction, startup reconciliation, and delivery-cache downgrade.

- [ ] **Step 2: Run tests and verify imports fail**

Run: `.venv/bin/pytest tests/test_discord_task_model.py tests/test_discord_task_store.py -q`

Expected: collection errors because the modules do not exist.

- [ ] **Step 3: Implement schema version 1 and atomic persistence**

Write `{"version": 1, "tasks": {"<uuid>": {...}}}` via a mode-`0600` temporary file, `fsync`, and `os.replace`. Mutate a copied mapping and publish it in memory only after the disk replace succeeds.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_discord_task_model.py tests/test_discord_task_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_model.py src/study_discord_agent/discord_task_store.py tests/test_discord_task_model.py tests/test_discord_task_store.py
git commit -m "feat(discord): persist task lifecycle state"
```

### Task 4: Authorization And Safe Failure Classification

**Files:**
- Create: `src/study_discord_agent/discord_task_auth.py`
- Create: `src/study_discord_agent/discord_task_failures.py`
- Create: `tests/test_discord_task_auth.py`
- Create: `tests/test_discord_task_failures.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DiscordTaskAccess:
    actor_id: int
    guild_id: int
    visible_channel_ids: frozenset[int]
    manageable_channel_ids: frozenset[int]

class DiscordTaskAction(StrEnum):
    VIEW = "view"; WHY_FAILED = "why_failed"; STEER = "steer"; STOP = "stop"
    RETRY = "retry"; CONTINUE = "continue"; FORGET = "forget"

def authorize(record: DiscordTaskRecord, action: DiscordTaskAction, access: DiscordTaskAccess) -> None: ...
def classify_agent_failure(error: BaseException, *, persisted_session: bool, active_turn: bool) -> DiscordTaskFailure: ...
def classify_delivery_failure(*, definitive_non_delivery: bool) -> DiscordTaskFailure: ...
```

- [ ] **Step 1: Write failing table-driven tests** for cross-guild/revoked visibility, owner actions, moderator Stop-only, requester-only Why/Forget, and every typed exception-to-safe-copy/retry-mode mapping.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_task_auth.py tests/test_discord_task_failures.py -q` and expect import failures.
- [ ] **Step 3: Implement pure policy and constant safe summaries** without parsing or persisting `str(error)`.
- [ ] **Step 4: Rerun the focused tests** and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_auth.py src/study_discord_agent/discord_task_failures.py tests/test_discord_task_auth.py tests/test_discord_task_failures.py
git commit -m "feat(discord): authorize task actions and classify failures"
```
