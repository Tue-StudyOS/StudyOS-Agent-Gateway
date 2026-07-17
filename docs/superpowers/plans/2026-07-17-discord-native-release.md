# Discord Native Task Verification And Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the native Discord task surface against the exact pinned Codex app server, publish the reviewed branch, and safely deploy the verified image to the StudyOS Jetson.

**Architecture:** Package a deterministic schema/startup check and an authenticated lifecycle smoke. Build and smoke the candidate image against the existing auth volume before replacing production, then verify state retention, health, runtime version, command registration, and task behavior after deployment.

**Tech Stack:** Python 3.12, Codex CLI 0.144.1, Docker, Bash, SSH/rsync, Git/GitHub, Discord REST/UI smoke.

## Global Constraints

- Complete the core and surface plans first.
- The pinned target is exactly `codex-cli 0.144.1`; a different host CLI is not evidence.
- Never print, copy into Git, or bake Discord/GitHub/Codex credentials into an image.
- Preserve `/auth/codex`, GitHub auth volumes, `/workspaces`, artifacts, attachments, task JSON, and course/automation memories.
- Candidate schema/authenticated smoke must pass before the live container is removed.
- Deployment failure must leave the existing live container running.
- Automatic PR review/comment prompts remain disabled; read-only PR Discord notifications remain.
- Do not claim a Discord interaction smoke if only REST registration or unit tests were checked.

---

### Task 10: Pinned App-Server Contract And Authenticated Smoke CLI

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

### Task 11: Disable Automatic PR Commenting

**Files:**
- Modify: `src/study_discord_agent/github_events.py`
- Modify: `tests/test_github_events.py`
- Modify: `README.md`
- Modify: `docs/agent-runtime.md`

- [ ] **Step 1: Change the PR webhook test** to keep its Discord title, URL, and review invitation while asserting `notification.agent_prompt is None` for opened, reopened, ready-for-review, synchronize, and closed actions.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_github_events.py -q` and verify the opened-PR case fails because it still creates a comment-capable agent prompt.
- [ ] **Step 3: Remove automatic PR agent prompts** from `_pull_request_notification`; preserve the Discord embed/follow-up and issue refinement behavior. Document that PR comments now require an explicit human request.
- [ ] **Step 4: Rerun** `.venv/bin/pytest tests/test_github_events.py -q` and expect PASS.
- [ ] **Step 5: Commit**

```bash
git add src/study_discord_agent/github_events.py tests/test_github_events.py README.md docs/agent-runtime.md
git commit -m "fix(github): disable automatic PR comments"
```

### Task 12: Documentation, Defaults, And Full Local Verification

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
- Set and document `AGENT_TIMEOUT_SECONDS=1800` while preserving the typed timeout interrupt-grace behavior.
- Document that startup incompatibility is fatal and runtime recovery never invokes a one-shot fallback.

- [ ] **Step 1: Update focused documentation/default tests** to assert 30-minute config, exact command names, fixed task-store path, and pinned smoke command.
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

### Task 13: Review, Push, Candidate Preflight, And Jetson Deployment

**Files:**
- Review only: all branch changes against the approved spec and both implementation plans.

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

Over SSH, record the current container/image, `codex --version`, health, task-store presence, Codex login status, automation statuses, and SHA256 hashes/counts for course and automation memory files. Create a timestamped tar backup of the `studyos-agent-gateway_codex-auth` volume. Set live `AGENT_AUTO_REVIEW_ENABLED=false` before restart and pause any active automation whose prompt can write PR comments; preserve read-only PR notifications.

- [ ] **Step 4: Deploy the commit-tagged candidate**

```bash
IMAGE_TAG="studyos-agent-gateway:jetson-$(git rev-parse --short HEAD)" scripts/deploy_jetson.sh
```

Expected: candidate image build, exact-version schema smoke, authenticated lifecycle/recovery smoke, container replacement, and `/health` all succeed in that order.

- [ ] **Step 5: Verify the live result**

Confirm the running image tag/SHA, `codex-cli 0.144.1`, `codex login status`, `AGENT_AUTO_REVIEW_ENABLED=false`, no active PR-writing automation, clean startup logs, `{"status":"ok"}`, matching memory hashes/counts, and registered guild commands `study` plus `Ask StudyOS about this`. Exercise `/study status` and one safe failed-task Why/Retry path in the StudyOS test channel; report any UI step not actually performed.
