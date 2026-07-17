# Discord Native Task Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the durable task core through mention chat, three slash commands, one message context action, persistent Components V2 cards, safe recovery actions, and bounded file handling.

**Architecture:** `DiscordTaskService` is the only execution coordinator. Thin message/command/modal adapters create typed requests; a pure card renderer plus one global `DynamicItem` router resolves task IDs from the store after restart; a presenter owns Discord I/O and never determines authorization.

**Tech Stack:** Python 3.12, discord.py application commands/modals/DynamicItem/LayoutView, asyncio, pytest/pytest-asyncio.

## Global Constraints

- Complete the core plan first; use its task model, store, authorization, failure, execution, and recovery interfaces.
- Cards contain no raw prompt, command, traceback, stderr, token, reasoning, or host path.
- Components use durable task IDs; never encode or trust owner/state in a custom ID.
- Modal display is always the initial interaction response; I/O callbacks defer within three seconds.
- V2 cards use `TextDisplay`/`Container` and no message content or embeds.
- Input is limited to 10 attachments of 8,000,000 bytes each and cleaned on every terminal path.
- Every public send/edit disables allowed mentions; all caller-specific responses are ephemeral.
- Use TDD and keep modules roughly below 300 lines.

---

### Task 5: Staged Inputs And Definitive Delivery Cache

**Files:**
- Create: `src/study_discord_agent/discord_task_inputs.py`
- Create: `src/study_discord_agent/discord_delivery_cache.py`
- Modify: `src/study_discord_agent/discord_files.py`
- Create: `tests/test_discord_task_inputs.py`
- Create: `tests/test_discord_delivery_cache.py`

**Interfaces:**

```python
@dataclass
class StagedDiscordAttachments:
    paths: tuple[Path, ...]
    directory: Path | None
    def cleanup(self) -> None: ...

async def stage_message_attachments(
    message: discord.Message, root: Path, *, trigger_event_id: int,
) -> StagedDiscordAttachments: ...

class DiscordDeliveryCache:
    def put(self, task_id: str, reply: PreparedDiscordReply) -> None: ...
    def consume(self, task_id: str, allowed_roots: tuple[Path, ...], max_bytes: int) -> PreparedDiscordReply | None: ...
    def discard(self, task_id: str) -> None: ...
    def close(self) -> None: ...
```

- [ ] **Step 1: Write failing tests**

```python
async def test_staging_rejects_eleventh_or_oversize_file_and_cleans_partial(tmp_path): ...
def test_cache_revalidates_roots_size_and_existence_before_consuming(tmp_path): ...
def test_cache_deletes_generated_reply_on_discard_and_shutdown(tmp_path): ...
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_task_inputs.py tests/test_discord_delivery_cache.py -q` and expect import failures.
- [ ] **Step 3: Implement single-owner staging/cache cleanup**; validate declared size before download and actual size after save, and never cache a partially or ambiguously sent reply.
- [ ] **Step 4: Rerun the focused tests** and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_inputs.py src/study_discord_agent/discord_delivery_cache.py src/study_discord_agent/discord_files.py tests/test_discord_task_inputs.py tests/test_discord_delivery_cache.py
git commit -m "feat(discord): bound task attachments and delivery retries"
```

### Task 6: Shared Discord Task Service

**Files:**
- Create: `src/study_discord_agent/discord_task_request.py`
- Create: `src/study_discord_agent/discord_task_service.py`
- Create: `tests/test_discord_task_service.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DiscordTaskRequest:
    source_kind: DiscordTaskSourceKind
    guild_id: int
    origin_channel_id: int
    execution_channel_id: int
    owner_id: int
    trigger_event_id: int
    source_message_id: int | None
    prompt: str
    source_label: str
    attachments: StagedDiscordAttachments
    origin_context: DiscordOriginContext | None

class DiscordTaskPresentation(Protocol):
    async def create_card(self, record: DiscordTaskRecord) -> int | None: ...
    async def render_card(self, record: DiscordTaskRecord) -> None: ...
    async def prepare_reply(self, record: DiscordTaskRecord, reply: AgentReply) -> PreparedDiscordReply: ...
    async def deliver_reply(self, record: DiscordTaskRecord, reply: PreparedDiscordReply) -> int: ...
    def progress_sink(self, task_id: str) -> ProgressSink: ...

class DiscordTaskService:
    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord: ...
    async def steer(self, task_id: str, access: DiscordTaskAccess, prompt: str, interaction_id: int) -> DiscordTaskRecord: ...
    async def stop(self, task_id: str, access: DiscordTaskAccess, interaction_id: int) -> DiscordTaskRecord: ...
    async def retry(self, task_id: str, access: DiscordTaskAccess, interaction_id: int) -> DiscordTaskRecord: ...
    async def continue_task(self, parent_id: str, access: DiscordTaskAccess, request: DiscordTaskRequest, interaction_id: int) -> DiscordTaskRecord: ...
    def status(self, task_id: str, access: DiscordTaskAccess) -> DiscordTaskRecord: ...
    def list_tasks(self, access: DiscordTaskAccess, scope: str, state: str, current_channel_id: int) -> tuple[DiscordTaskRecord, ...]: ...
```

- [ ] **Step 1: Write failing service tests** for one active task/channel, parallel channels, no lock over I/O, duplicate interactions/CAS, Stop-vs-completion/timeout, typed failure cards, generic same-session Retry without original prompt, definitive delivery Retry without agent execution, ambiguous delivery with no Retry, latest-only linked Continue, missing card, Forget, and cleanup.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_task_service.py -q` and expect import failures.
- [ ] **Step 3: Implement orchestration** with state reservations before I/O, the first interruption cause winning, `recovering` before runtime recovery, `delivering` before output, and background task ownership until cleanup.
- [ ] **Step 4: Rerun** `.venv/bin/pytest tests/test_discord_task_service.py -q` and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_request.py src/study_discord_agent/discord_task_service.py tests/test_discord_task_service.py
git commit -m "feat(discord): coordinate durable agent tasks"
```

### Task 7: Canonical Cards And Restart-Safe Components

**Files:**
- Create: `src/study_discord_agent/discord_task_cards.py`
- Create: `src/study_discord_agent/discord_task_components.py`
- Modify: `src/study_discord_agent/discord_progress.py`
- Delete: `src/study_discord_agent/discord_progress_view.py`
- Create: `tests/test_discord_task_cards.py`
- Create: `tests/test_discord_task_components.py`
- Modify: `tests/test_discord_progress.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class DiscordTaskControls:
    steering: bool
    resumable: bool

def build_task_card(record: DiscordTaskRecord, progress: AgentProgress | None, controls: DiscordTaskControls) -> discord.ui.LayoutView: ...

class DiscordTaskActionItem(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.LayoutView]],
    template=r"^studyos:task:(?P<action>stop|add_context|retry|why|continue):(?P<task_id>[0-9a-f]{32})$",
): ...

class DiscordTaskCardMessenger(DiscordTaskPresentation): ...
```

- [ ] **Step 1: Write failing renderer/router tests** for every state, terminal Stop removal, safe Why/Retry, capability-based Add/Continue, result URL, anchored dynamic IDs, reconstruction after restart, owner/mod authorization, modal-first actions, stale/unknown IDs, and allowed-mention suppression.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_task_cards.py tests/test_discord_task_components.py tests/test_discord_progress.py -q` and expect import failures.
- [ ] **Step 3: Implement the pure card and global DynamicItem router**. Callbacks resolve the task afresh and ask the service to rerender; they never mutate a reconstructed stale LayoutView.
- [ ] **Step 4: Rerun focused tests** and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_cards.py src/study_discord_agent/discord_task_components.py src/study_discord_agent/discord_progress.py tests/test_discord_task_cards.py tests/test_discord_task_components.py tests/test_discord_progress.py
git rm src/study_discord_agent/discord_progress_view.py
git commit -m "feat(discord): add persistent task cards and controls"
```

### Task 8: Slash Commands, Context Action, And Permission Resolution

**Files:**
- Create: `src/study_discord_agent/discord_task_access.py`
- Create: `src/study_discord_agent/discord_task_commands.py`
- Create: `src/study_discord_agent/discord_task_controller.py`
- Create: `tests/test_discord_task_access.py`
- Create: `tests/test_discord_task_commands.py`

**Interfaces:**

```python
class StudyCommandGroup(app_commands.Group):
    # /study ask, /study tasks, /study status with status autocomplete
    ...

class TaskInstructionModal(discord.ui.Modal):
    instruction = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=4000)

def create_message_context_menu(controller: DiscordTaskController) -> app_commands.ContextMenu: ...
async def resolve_task_access(interaction: discord.Interaction, record: DiscordTaskRecord) -> DiscordTaskAccess: ...
```

- [ ] **Step 1: Write failing tests** for declared commands/context menu, modal-first behavior, deferral, at-most-10 autocomplete, manual-ID authorization, same-guild/current-view checks including private threads, thread permission/type rejection with no parent fallback, ephemeral task/status output, and inactive Forget confirmation.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_task_access.py tests/test_discord_task_commands.py -q` and expect import failures.
- [ ] **Step 3: Implement commands and reusable modal**. A dedicated thread uses a neutral name, stores parent origin/new execution IDs, and never includes prompt text in its name or persisted record.
- [ ] **Step 4: Rerun focused tests** and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_task_access.py src/study_discord_agent/discord_task_commands.py src/study_discord_agent/discord_task_controller.py tests/test_discord_task_access.py tests/test_discord_task_commands.py
git commit -m "feat(discord): add native task commands and modals"
```

### Task 9: Mention Adapter, Bot Wiring, And Restart Reconciliation

**Files:**
- Rewrite: `src/study_discord_agent/discord_mentions.py`
- Modify: `src/study_discord_agent/discord_bot.py`
- Modify: `src/study_discord_agent/main.py`
- Modify: `tests/test_discord_mentions.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `tests/test_discord_bot_messages.py`

**Interfaces:**
- `DiscordMentionCoordinator` extracts/stages input and delegates only to `DiscordTaskService`.
- `StudyBot.setup_hook()` registers `DiscordTaskActionItem`, copies global commands to a configured guild before sync, never clears the tree, and starts one post-ready reconciliation task.

- [ ] **Step 1: Replace closure-era tests** with mention start, owner follow-up steer, other-user active-card guidance, owner text Stop, duplicate message suppression, shared request path, guild/global sync, global DynamicItem registration, and startup card reconciliation.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_discord_mentions.py tests/test_discord_bot.py tests/test_discord_bot_messages.py -q` and verify failures against the old coordinator/tree clearing.
- [ ] **Step 3: Wire service/presenter/controller/store once**. Reconcile active/delivering records before rerendering every affected reachable card; inaccessible cards remain store-authoritative and log only safe task IDs.
- [ ] **Step 4: Run all Discord tests** with `.venv/bin/pytest tests/test_discord_*.py -q` and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/discord_mentions.py src/study_discord_agent/discord_bot.py src/study_discord_agent/main.py tests/test_discord_mentions.py tests/test_discord_bot.py tests/test_discord_bot_messages.py
git commit -m "feat(discord): wire native task control into the bot"
```
