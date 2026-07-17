# Capability Runner Protocol and Android Reference Runner

**Date:** 2026-07-17
**Status:** Approved direction; awaiting written-spec review
**Scope:** Public execution-provider contract, gateway client, and Android reference runner

## Decision

Keep the Jetson gateway image as the credential-bearing control plane. Add a small client
there and run heavyweight UI toolchains as separate, registered services. The first
provider implements Flutter/Android build, launch, interaction, diagnostics, and
screenshots on x86_64 Linux with KVM or a dedicated Android device.

Providers may be Compose-network services or authenticated private endpoints. Both use
the same versioned protocol. The gateway never mounts the Docker socket, imports provider
code, or sends Discord, Codex, or GitHub credentials to a provider.

The public runner protocol is the extension point delivered here. Task connectors such
as a web UI, LMS, or another chat service remain a separate design.

## Problem

The Linux/ARM64 gateway edits repositories and returns images to Discord, but has no UI
toolchain or device control. Adding these would bloat every deployment, run build hooks
beside credentials, and still not support an Android emulator on the Jetson host.

The missing boundary is a tool the active agent can call repeatedly during one turn to
sync the exact worktree, analyze/test/build, launch and control a leased device, fetch and
inspect evidence, then edit and repeat before replying in Discord.

## Goals

- Keep `Dockerfile.agent` free of heavyweight UI toolchains while letting Codex drive an
  iterative build-launch-inspect-screenshot loop.
- Support a linked container and a remote worker through the same client contract.
- Keep the contract language-neutral and testable without Discord or Codex; ship Android
  first without coupling the protocol to it.
- Preserve exact per-channel worktree isolation and the existing Discord artifact path.
- Fail explicitly when a capability, device, repository shape, or worker is unavailable;
  keep implementation modules focused and roughly below 300 lines.

## Non-goals

- iOS, Xcode, or macOS execution.
- A browser/web reference provider in the first implementation.
- A connector SDK or dynamically loaded gateway plugins.
- A sandbox for hostile or mutually untrusted repositories.
- Device farms, device matrices, video recording, or visual regression baselines.
- Arbitrary shell-command submission through the provider API.
- Project secrets, signing credentials, production API keys, or app-store publishing.
- Automatic installation or execution of unregistered provider images.

## Architecture

### 1. Provider client and CLI

The gateway package adds a framework-neutral `studyos-runner` CLI. Codex invokes it with
normal command execution, so no new app-server or MCP dependency is required. Other agent
runtimes can use the same executable.

The client owns registry loading, capability/version negotiation, safe source snapshots,
authenticated requests, bounded polling, signal cancellation, and checksum-verified
downloads under `/tmp/studyos-artifacts/runners/<session-id>/`. It provides concise human
output and stable JSON output for agents.

The initial commands are:

```text
studyos-runner doctor
studyos-runner open --capability android [--provider <id>] --workspace <path> [--project-root <path>]
studyos-runner sync <session-id> --workspace <path>
studyos-runner run <session-id> <action> [typed action options]
studyos-runner artifacts <action-id> --output <allowed-directory>
studyos-runner close <session-id>
```

`run` blocks by default, streams bounded status, and exits nonzero on failure. `--json`
returns the typed result without logs mixed into stdout.

### 2. Provider registry

Providers are registered statically by an administrator in a mounted TOML file. Docker
service discovery and the Docker socket are not used.

```toml
schema_version = 1
repositories = [{ id = "Tue-StudyOS/example-app", git_common_dir = "/workspaces/Tue-StudyOS/example-app/.git" }]

[[providers]]
id = "android"
url = "http://android-runner:8090"
token_env = "STUDYOS_ANDROID_RUNNER_TOKEN"
allow_insecure_http = true
allowed_repository_prefixes = ["Tue-StudyOS/"]
```

Tokens remain environment secrets. TLS is the default; plaintext requires the explicit
per-endpoint flag and is intended only for a private container network. Provider IDs are
unique. Missing tokens, duplicates, unsupported schemas, and malformed URLs are errors.

Operators add other OCI services by registering endpoints. If multiple providers match a
capability, `open` requires `--provider`. Repository identity comes from an
administrator-owned mapping of exact Git common directories to GitHub repository IDs,
not uploaded metadata or a mutable remote URL. Our deployment enables only Android and
approved StudyOS repositories.

### 3. Versioned provider API

The reference models and generated OpenAPI document define protocol version `1`:

```text
GET    /health
GET    /v1/capabilities
POST   /v1/sessions
PUT    /v1/sessions/{session_id}/snapshot
POST   /v1/sessions/{session_id}/actions
GET    /v1/actions/{action_id}
POST   /v1/actions/{action_id}/cancel
GET    /v1/actions/{action_id}/artifacts/{artifact_id}
DELETE /v1/sessions/{session_id}
```

All `/v1` routes require a bearer token. Responses include the protocol version and never
expose host paths, environment values, raw exceptions, or credentials. Session and action
creation require a client-generated `Idempotency-Key`; retries return the original object.

`/v1/capabilities` reports provider identity, implementation version, platform, supported
actions, toolchain versions, maximum snapshot and artifact sizes, concurrency, and device
availability. Unsupported actions are rejected before source transfer.

A session owns one source tree and, after launch, one device lease. V1 serializes every
action per session rather than classifying reads and mutations. Session states are
`empty`, `ready`, `launched`, `closed`, and `expired`; action states are `pending`,
`running`, `succeeded`, `failed`, `cancelled`, and `timed_out`. Results contain a safe
summary, bounded diagnostics, timestamps, and artifact descriptors with opaque ID, media
type, size, and SHA-256. Downloads must belong to the named action and session.

The v1 Android actions are `analyze`, `test`, `build`, `launch`, `stop`, `logs`,
`ui_tree`, `screenshot`, `tap`, `swipe`, `enter_text`, and `keyevent`.
Analyze/test/build require `ready`; launch requires a successful current-snapshot build
and a free device; logs/UI/input/stop require `launched`, and stop returns `ready`.

Each action has a fixed request model. There is no command string or executable field.
Coordinate inputs are bounded to the current screen. Artifact names are server-generated;
the optional user label is sanitized and never becomes a path.

### 4. Safe source snapshots

The client enumerates tracked and non-ignored untracked files using Git. It rejects
symlinks, gitlinks/submodules, device files, absolute/traversal paths, and files outside
the worktree. Known-sensitive paths such as `.env*`, signing files, SSH keys, auth
directories, `.git`, ignored files, build output, and the registry are excluded.

The wire format is multipart: RFC 8785 JSON manifest plus deterministic gzip/PAX tar.
Manifest paths sort lexicographically and include size, SHA-256, and normalized mode
`0644` or `0755`; tar uid/gid/mtime and the gzip mtime are zero. Only manifest regular
files appear in the tar. Repository identity, commit, normalized relative `project_root`,
and bundle SHA-256 are manifest fields. The provider enforces limits, extracts into a
random mode-`0700` directory as an unprivileged user, and verifies every field/checksum.

`sync` replaces the session snapshot only while no action is running. It preserves
toolchain caches but invalidates application build output and stops a launched app. This
makes the post-edit state unambiguous.

Filename rules cannot prove source is secret-free. V1 therefore accepts only repositories
that operators declare trusted and secret-free; projects needing runtime secrets fail.

### 5. Android reference provider

The separate reference image contains pinned Flutter stable, JDK, Android command-line
and platform/build tools, one API-level emulator image, and ADB. SDK, Pub, and Gradle
caches use provider-only volumes; sessions and artifacts have TTL cleanup. The default
Compose deployment remains gateway-only; an opt-in override adds this x86_64 service.

It supports two explicit deployment modes:

- `emulator`: x86_64 Linux with `/dev/kvm` and a configured AVD;
- `device`: one ADB serial over USB or paired TLS wireless debugging on a private network.

The mode is required. The provider never falls back from an emulator to an arbitrary
connected device. Startup fails if the selected device, KVM, SDK license state, or pinned
toolchain is unavailable.

Repository support in v1 is intentionally narrow:

- a conventional Flutter project with an Android target, including a unique project such
  as `flutter_app/` below the worktree; or
- a conventional single-application-module Gradle Android project.

The client may pass an inspected `project_root`; otherwise the provider accepts only one
unambiguous candidate. Multiple Flutter projects, custom entrypoints, product flavors,
multiple application modules, or custom signing are explicitly unsupported. There is no
speculative command configuration.

Flutter projects use pinned `flutter analyze`, `flutter test`, debug APK build, install,
and launch paths. Conventional Gradle projects use the wrapper for lint, unit tests, and
debug assembly. The provider derives the debug APK and application ID from build output;
it does not accept them from an untrusted request.

ADB supplies screenshots, UI hierarchy, bounded logcat, input events, app stop, and
device reset. Each session starts from known device state. Closing or expiry stops and
clears the app, releases the lease, and removes its source and artifacts.

On startup, the provider reconciles a minimal persisted lease/app record, kills stale
processes, clears abandoned session roots, and resets the configured emulator or device.
Build/test children receive an allowlisted environment containing no provider auth, ADB
keys, service configuration, or credential-bearing caches.

### 6. Agent guidance and Discord delivery

The agent image receives only the CLI, registry path, and a small seeded skill. Its
guidance requires analyze/tests before launch, inspection of returned screenshots, and
exact reporting when visual verification is unavailable.

Downloaded PNGs already sit under an allowed Discord artifact root. The agent can inspect
them within the active turn and include selected before/after images in its existing final
artifact response. The provider never talks to Discord directly.

## End-to-end flow

1. A Discord request gets its existing channel-specific worktree; Codex edits it.
2. Codex runs `doctor` and `open`; the client verifies policy and uploads the snapshot.
3. Codex runs analyze, tests, build, launch, UI inspection, interaction, and screenshot.
4. The client downloads evidence to the artifact root and Codex inspects the image.
5. Further edits use `sync` and repeat the checks in the same session.
6. Codex closes the session and attaches final evidence to the normal Discord reply.

## Reliability and failure behavior

- Provider/protocol/capability absence, busy device, invalid snapshot, build failure,
  disconnect, timeout, and corrupt artifact are distinct typed failures.
- One Android provider exposes one device lease. Concurrent launch requests receive a
  typed busy response with `retry_after`; v1 does not create an unbounded queue.
- Client interruption cancels best-effort; provider deadlines and TTLs ensure cleanup.
- Actions run in their own process groups. Cancellation kills the complete build or ADB
  process group before the action reaches a terminal state.
- Provider unavailability does not make the Discord gateway unhealthy. The agent may
  continue code-only work but must not claim build, device, or screenshot verification.
- `doctor` reports provider health; gateway `/health` remains gateway-only.

## Security boundaries

- Providers receive source and task metadata only, never gateway volumes or credentials.
- The provider runs nonroot with a read-only root filesystem where practical, dropped
  capabilities, explicit cache/session mounts, resource limits, and no Docker socket.
- KVM or USB access is granted only to the Android provider service and documented as a
  high-trust host capability.
- Provider registration is explicit. The gateway holds the bearer token; provider config
  holds only its SHA-256 digest for constant-time verification. Remote transport uses TLS.
- Exact canonical-clone identity and repository policy are checked before upload.
- V1 is a trusted-repository worker, not a hostile multi-tenant sandbox. It still treats
  uploaded structure and build output defensively with bounds, validation, and cleanup.
- The protocol is extensible through new capability names and version negotiation, not by
  accepting arbitrary executable requests.

## Testing

Focused tests cover:

- registry parsing, URL/token rules, capability selection, and protocol mismatch;
- snapshot enumeration, dirty files, limits, sensitive exclusions, invalid paths, checksums, and extraction;
- every request/response model and typed error mapping;
- client polling, cancellation, timeout, artifact checksum/size, and cleanup;
- session/action states, idempotency, one-device lease, busy response, TTL/restart cleanup, and termination;
- Flutter/Gradle project detection, including ambiguous and unsupported layouts;
- Android command construction/output parsing with fake executors and ADB;
- an in-process contract test with a tiny fixture snapshot;
- existing Discord artifact delivery with a downloaded PNG.

CI runs `ruff check .`, `pyright`, and `pytest`. It also builds the lightweight client
image and validates the provider OpenAPI/conformance fixtures. A real emulator smoke is a
separate opt-in job on an x86_64 KVM runner; it builds a tiny fixture app, launches it,
dumps the UI, captures a PNG, and verifies cleanup. The branch is not claimed production
ready until that smoke has run on actual supported hardware.

## Success criteria

1. The default gateway image contains no Flutter, Android SDK, emulator, or browser.
2. A registered provider is discoverable through the CLI with pinned version details.
3. A dirty StudyOS worktree reaches the provider without ignored or known-sensitive paths.
4. The Android provider analyzes, tests, builds, launches, inspects, interacts with, and screenshots a supported Flutter Android app.
5. The screenshot is checksum-verified, inspected by Codex, and delivered through the existing Discord artifact flow.
6. Stop, timeout, disconnect, and restart paths release the device and remove session data.
7. An unsupported host or repository shape fails explicitly without a fallback device, command, or mock result.
8. A third-party service passes public conformance tests without importing gateway internals.

## Delivery sequence

1. Protocol models, registry, client abstraction, and conformance fixtures.
2. Snapshot builder and artifact downloader.
3. `studyos-runner` CLI plus agent guidance.
4. Android provider service with fake executor/ADB tests.
5. Separate Android provider image and deployment documentation.
6. Gateway/client integration tests and a real x86_64 KVM smoke.

The Jetson deployment gains the client and provider configuration only. Actual Android
capability becomes available when an operator registers a supported x86_64/KVM provider
or a provider with one explicitly configured physical Android device.
