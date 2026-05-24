# StudyOS Collaboration Workflow Notes

These notes capture the current working model for the StudyOS Discord/GitHub
agent gateway. They are intentionally draft-level: the course can tune review
rules, schedules, and channels once 20-30 students are actively using the repo.

## Workflow Shape

Discord is the conversational surface. Students tag the bot when they want to
brainstorm, clarify architecture, refine an issue, or ask for technical help.
GitHub remains the source of truth for issues, pull requests, review state, CI,
and final merges.

Issues should become scoped work before implementation. The agent should help
ask clarifying questions, identify duplicates, split overly large tickets, and
write acceptance criteria. Once a ticket is clear, the agent can propose a PR
plan.

Implementation is human-gated. The agent should start a branch or PR only when
a student explicitly asks for it, for example by tagging the bot in Discord with
"implement issue #12" or by commenting in GitHub that the agent should start.
Readiness alone is not permission to implement.

Pull requests should be reviewed by students. The agent can summarize PRs,
explain design tradeoffs, answer review questions, and respond to requested
changes, but humans approve and merge.

## Review Policy Draft

- Feature PRs: aim for 2 student reviewers.
- Docs, examples, or small maintenance PRs: 1 reviewer is usually enough.
- Large architecture changes: 2 reviewers plus at least one maintainer/course
  coordinator check.
- Authors should not count as their own reviewer.
- The agent is not an approving reviewer. It can prepare review context and fix
  issues, but final approval belongs to humans.

Reviewer nudges should be friendly and low-pressure. Prefer channel posts over
direct pings at first. Tag specific people only when they are already assigned,
the PR is blocked, or the group agrees to a reviewer rotation.

## Discord Channel Routing

Current implementation has one configured outbound GitHub channel:
`DISCORD_PR_CHANNEL_ID`. GitHub webhook notifications, poller summaries, and
agent triage summaries post there.

Future channel routing could split:

- `#github`: new PRs, issue activity, CI summaries, review nudges.
- `#agent-lab`: bot brainstorming and agent coordination.
- `#release`: weekly digest, milestones, deploy readiness.

Until channel routing exists, keep automation output short and post only
actionable items to the configured GitHub channel.

## Webhooks Vs Automations

Use GitHub webhooks for low-latency event posts:

- new PR opened
- PR marked ready for review
- review submitted
- issue opened or commented
- CI failure if webhook coverage is added later

Use Codex automations as the slower coordination layer:

- recurring triage of issues and PR comments
- stale review reminders
- finding implementation-ready issues
- weekly progress digest
- maintaining a long-lived coordinator thread

If webhooks are not configured, a 15-30 minute GitHub triage automation can be
the backstop for posting new PRs and review needs. Once webhooks are configured,
the triage automation should become less chatty and focus on synthesis.

## Suggested Automation Set

`studyos-github-triage`: every 30 minutes. Checks new or recently updated
issues, PRs, review comments, and blockers. Good default while the repo is
active.

`studyos-pr-review-nudge`: every 2 hours. Finds PRs missing reviewers, stale
review threads, or PRs waiting on CI. During heavy course activity, this might
move to hourly; during quiet periods, 4 hours is enough.

`studyos-issue-refinement`: every 6 hours. Finds vague or duplicate issues and
suggests concise clarifying questions. This should avoid noisy comments.

`studyos-implementation-candidates`: daily. Finds issues that are clear enough
for implementation and proposes small PR plans. It should not implement from
the automation itself.

`studyos-coordinator-thread`: heartbeat every 30 minutes. Uses a fixed
`target_thread_id` so the same Codex thread retains coordination context. This
needs a real thread ID before enabling.

`studyos-group-channel-digest`: daily at 17:00. Summarizes meaningful group
channel activity into the shared updates channel when there is something useful
for the wider course.

`studyos-weekly-digest`: weekly Thursday at 16:00. Summarizes merged work, open
review load, blockers, stale issues, and next milestones.

## Heartbeat Thread IDs

Heartbeat automations need a real `target_thread_id` to continue the same Codex
thread across runs. The seeded automation intentionally ships with
`REPLACE_WITH_CODEX_THREAD_ID` and should stay paused until that value is
replaced.

The thread ID cannot be usefully invented in advance. Use an existing Codex
thread or create the coordinator thread once, copy its ID, then update the
heartbeat automation TOML. Keeping the ID in memory is fine, but the active
automation still needs the explicit TOML field.

## Lightweight Image Tools

The image should stay focused: Discord listener, GitHub CLI, Codex CLI, Git,
SSH, Python runtime, and Node/npm for Codex. Avoid browsers for now.

Useful low-cost additions:

- `ripgrep`: fast repo search for agents and humans.
- `jq`: parse `gh` JSON output reliably.
- `fd` or `fd-find`: quick file discovery.
- `less`: inspect long command output interactively.
- `procps`: inspect running processes inside the container.
- `unzip`: handle downloaded artifacts or release bundles.
- `make`: useful when course repos provide simple task entrypoints.

Avoid by default:

- Chromium or Playwright browser stacks.
- Full compiler toolchains unless the course repo needs them.
- Cloud SDKs baked into the image; prefer mounting credentials and installing
  provider CLIs only when deployment workflow is clear.

## Open Questions

- Should review assignment be manual, round-robin, or based on touched areas?
- Should the bot tag reviewers immediately or wait until a PR has been stale
  for a threshold such as 4-6 hours?
- Do we need separate Discord channel IDs for PR posts, triage summaries, and
  weekly digests?
- Should schedules pause outside course working hours?
