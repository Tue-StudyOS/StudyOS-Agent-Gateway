# Discord Native Task Verification And Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the native Discord task surface and passive GitHub PR/issue intake against
the exact pinned Codex app server, publish the reviewed branch, and safely deploy the
verified image to the StudyOS Jetson.

**Architecture:** Package a deterministic schema/startup check and an authenticated
lifecycle smoke. Build and smoke the candidate image against the existing auth volume
before replacing production, then verify state retention, health, runtime version,
command registration, passive mirror-card behavior, and explicit task intake after
deployment.

**Tech Stack:** Python 3.12, Codex CLI 0.144.1, Docker, Bash, SSH/rsync, Git/GitHub, Discord REST/UI smoke.

## Global Constraints

- Complete the core, surface, and
  [GitHub intake](2026-07-17-github-discord-intake-plan.md) plans first.
- The pinned target is exactly `codex-cli 0.144.1`; a different host CLI is not evidence.
- Never print, copy into Git, or bake Discord/GitHub/Codex credentials into an image.
- Preserve `/auth/codex`, GitHub auth volumes, `/workspaces`, artifacts, attachments, task JSON, and course/automation memories.
- Candidate schema/authenticated smoke must pass before the live container is removed.
- Deployment failure must leave the existing live container running.
- `DISCORD_PR_CHANNEL_ID` is the configured shared PR-and-issue `#pr-review` intake
  channel. GitHub webhooks may only upsert passive native cards there; they never invoke
  the agent, create a task, or write to GitHub.
- Review, Security review, and Vulnerability scan remain read-only/local-analysis tasks.
  Work on this requires modal instructions. Results stay in Discord by default, external
  writes require an exact explicit user instruction, and merge is never authorized.
- Do not claim a Discord interaction smoke if only REST registration or unit tests were checked.

---

### Task 13: Pinned App-Server Contract And Authenticated Smoke CLI

**Files:**
- Create: `src/study_discord_agent/app_server_smoke.py`
- Modify: `pyproject.toml`
- Create: `tests/test_app_server_smoke.py`
- Modify: `Dockerfile.agent`
- Modify: `scripts/deploy_jetson.sh`

**Interfaces:**

```python
EXPECTED_CODEX_VERSION = "codex-cli 0.144.1"
REQUIRED_REQUEST_METHODS = {
    "initialize", "thread/start", "thread/resume", "turn/start",
    "turn/steer", "turn/interrupt",
}
REQUIRED_NOTIFICATIONS = {
    "item/completed", "turn/completed", "thread/tokenUsage/updated",
}

async def run_schema_smoke(codex: Path) -> None: ...
async def run_authenticated_smoke(codex: Path, workdir: Path) -> None: ...
def main() -> None: ...
```

Register `studyos-codex-app-server-smoke = "study_discord_agent.app_server_smoke:main"`. The schema smoke runs `codex app-server generate-json-schema --experimental --out <temp>` and validates the method strings in `codex_app_server_protocol.schemas.json`.

The authenticated smoke uses a temporary executable named `codex` that writes its PID then `exec`s the real binary. It verifies initialize, source-less start, progress, same-thread Continue, immediate steer, interrupt, two concurrent active turns failing as `AgentRuntimeDisconnected` after `SIGKILL`, and one replacement process resuming both stored threads.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_schema_smoke_rejects_wrong_version_or_missing_method(...): ...
async def test_lifecycle_smoke_checks_same_thread_steer_interrupt_and_recovery(...): ...
def test_deploy_smokes_candidate_before_docker_rm():
    text = Path("scripts/deploy_jetson.sh").read_text()
    assert text.index("studyos-codex-app-server-smoke") < text.index("docker rm -f")
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_app_server_smoke.py -q` and expect import/behavior failures.
- [ ] **Step 3: Implement the CLI and mandatory candidate preflight**. Mount the existing Codex auth volume into the one-shot candidate container; do not require Discord or GitHub secrets for the smoke.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_app_server_smoke.py tests/test_skill_seed_files.py -q` and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/app_server_smoke.py pyproject.toml tests/test_app_server_smoke.py Dockerfile.agent scripts/deploy_jetson.sh
git commit -m "test(runtime): smoke the pinned Codex app server"
```

### Task 14: Enforce Passive GitHub Webhook Boundary And Intake Defaults

**Files:**
- Modify: `src/study_discord_agent/github_events.py`
- Modify: `src/study_discord_agent/discord_bot.py`
- Modify: `src/study_discord_agent/config.py`
- Modify: `.env.example`
- Modify: `tests/test_github_events.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `README.md`
- Modify: `docs/agent-runtime.md`

- [ ] **Step 1: Write passive-boundary regression tests**. For every supported PR and issue
  action, assert typed mirror/card metadata remains available while any compatibility
  `agent_prompt` is `None`. Assert webhook publication never calls `AgentGateway`, starts
  a task, or emits a GitHub write when a stale deployment environment still has
  `AGENT_AUTO_REVIEW_ENABLED=true`. Add a failing configuration assertion that the obsolete
  setting is no longer part of `Settings`.
- [ ] **Step 2: Test the four explicit intake defaults**. Review is read-only correctness,
  Security review is read-only auth/secrets/permissions/privacy/abuse analysis,
  Vulnerability scan is isolated local SAST/dependency analysis without active probing,
  and Work on this requires modal instructions. Results target the item thread in Discord;
  no prompt grants an external write unless the user's text explicitly requests that exact
  operation, and no path authorizes merge.
- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_github_events.py tests/test_discord_bot.py -q`.
  The passive-event assertions from Task 10 and action assertions from Task 11 should
  pass; if the obsolete setting still exists, only its configuration-surface assertion
  should fail.
- [ ] **Step 4: Remove all automatic webhook agent prompts and the
  `AGENT_AUTO_REVIEW_ENABLED` setting** from runtime configuration, examples, and docs so
  an old environment variable cannot re-enable the behavior. Preserve passive PR/issue
  card upserts and explicit interaction/mention intake only.
- [ ] **Step 5: Rerun focused tests** and expect PASS.
- [ ] **Step 6: Commit**

```bash
git add src/study_discord_agent/github_events.py src/study_discord_agent/discord_bot.py src/study_discord_agent/config.py .env.example tests/test_github_events.py tests/test_discord_bot.py README.md docs/agent-runtime.md
git commit -m "fix(github): keep webhook intake passive"
```

### Task 15: Documentation, Defaults, And Full Local Verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/agent-runtime.md`
- Modify: `docs/setup.md`
- Modify: `docs/security.md`
- Modify: `src/study_discord_agent/config.py`
- Modify: affected existing tests under `tests/`

**Requirements:**
- Document `/study ask`, `/study tasks`, `/study status`, message context action, task-card actions, safe failure details, latest-only Continue, retention/Forget, guild sync semantics, and app-server recovery.
- Document passive PR/issue mirror cards in the configured `DISCORD_PR_CHANNEL_ID`
  destination, the four explicit actions, item-thread execution/results, fixed read-only
  and safe-local-analysis boundaries, Work on this instructions, and the absence of
  automatic GitHub comments or other writes.
- Document that GitHub/external writes require an exact explicit user instruction, active
  probing is not part of Vulnerability scan, merge is prohibited, and the removed
  `AGENT_AUTO_REVIEW_ENABLED` flag cannot restore automatic behavior.
- Set and document `AGENT_TIMEOUT_SECONDS=1800` while preserving the typed timeout interrupt-grace behavior.
- Document that startup incompatibility is fatal and runtime recovery never invokes a one-shot fallback.

- [ ] **Step 1: Update focused documentation/default tests** to assert 30-minute config,
  exact command names, fixed task- and mirror-store paths, four exact mirror action labels,
  shared PR/issue channel semantics, removed auto-review setting, and pinned smoke command.
- [ ] **Step 2: Run the full suite before docs/default changes** with `.venv/bin/pytest -q` and record any failing assertions.
- [ ] **Step 3: Apply the documentation/default updates** without adding fallback configuration or mock production data.
- [ ] **Step 4: Run all required checks**

```bash
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/pytest -q
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/agent-runtime.md docs/setup.md docs/security.md src/study_discord_agent/config.py tests
git commit -m "docs: explain native Discord task control"
```

### Task 16: Review, Push, Candidate Preflight, And Jetson Deployment

**Files:**
- Review only: all branch changes against the Discord task-control and GitHub intake
  specs, plus the core, surface, GitHub intake, and release implementation plans.

- [ ] **Step 1: Run final review gates**

```bash
git diff --check origin/main...HEAD
.venv/bin/ruff check .
.venv/bin/pyright
.venv/bin/pytest -q
```

Expected: clean diff check and all quality gates exit 0.

- [ ] **Step 2: Push the exact reviewed branch**

```bash
git push -u origin codex/discord-native-task-control
```

Expected: remote branch points to local `HEAD`.

- [ ] **Step 3: Inspect and back up the live Jetson state**

Over SSH, record the current container/image, `codex --version`, health, task- and
mirror-store presence, Codex login status, automation statuses, and SHA256 hashes/counts
for course and automation memory files. Create a timestamped tar backup of the
`studyos-agent-gateway_codex-auth` volume. Remove the obsolete
`AGENT_AUTO_REVIEW_ENABLED` deployment setting and pause any active automation whose
prompt can automatically act on or write to PRs/issues; preserve passive mirror-card
publication.

- [ ] **Step 4: Deploy the commit-tagged candidate**

```bash
IMAGE_TAG="studyos-agent-gateway:jetson-$(git rev-parse --short HEAD)" scripts/deploy_jetson.sh
```

Expected: candidate image build, exact-version schema smoke, authenticated lifecycle/recovery smoke, container replacement, and `/health` all succeed in that order.

- [ ] **Step 5: Verify the live result**

Confirm the running image tag/SHA, `codex-cli 0.144.1`, `codex login status`, no automatic
PR/issue agent setting or writing automation, clean startup logs, `{"status":"ok"}`,
matching memory hashes/counts, registered guild commands `study` plus
`Ask StudyOS about this`, and restart-safe task/mirror controls.

Deliver one safe test PR event and one safe test issue event. Verify each upserts exactly
one passive native card in configured `#pr-review`, a repeated delivery does not duplicate
it, and neither delivery invokes the agent or posts to GitHub. As authorized users,
exercise Review, Security review, Vulnerability scan, and the Work on this instruction
modal; verify one item thread is reused, duplicate clicks do not duplicate work, results
remain in Discord, and no GitHub comment/review/label/assignment/push/close/merge occurs.
In that item context, exercise one explicit bot mention and one reply to a bot message;
verify both resolve the same typed repository/item without webhook-side execution. Also
exercise `/study status` and one safe failed-task Why/Retry path in the StudyOS test
channel. Report every UI or external-write boundary that was not actually verified.
