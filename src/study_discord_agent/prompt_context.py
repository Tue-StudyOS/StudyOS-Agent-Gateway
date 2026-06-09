from study_discord_agent.memory import get_studyos_memory_path


def build_agent_prompt(
    prompt: str,
    user: str,
    channel_id: int,
    codex_home: str | None,
    source_message_id: int | None = None,
    attachment_paths: tuple[str, ...] = (),
) -> str:
    memory_path = get_studyos_memory_path(codex_home)
    attachment_block = _build_attachment_block(attachment_paths)
    return (
        "You are running from the StudyOS Discord/GitHub collaboration gateway.\n"
        f"Before substantial StudyOS work, consult the project memory at {memory_path} "
        "for course context, product direction, collaboration policy, and tone. "
        "If the file is unavailable, continue with the request and mention missing "
        "context only when it affects the answer.\n"
        "Never merge pull requests; humans approve and merge.\n"
        "Do not add Co-authored-by, Generated-by, or similar agent attribution "
        "trailers to commits, PR bodies, issue comments, or release notes unless "
        "a human explicitly asks. Keep GitHub attribution on the authenticated "
        "repository user; do not list Codex, StudyOS Agent Gateway, or other "
        "agent runtimes as contributors.\n"
        "GitHub auth routing: the default `GH_CONFIG_DIR=/auth/gh` profile uses a "
        "fine-grained token for selected owned or course repositories; use it for "
        "normal work in those scoped repos. A second profile at "
        "`GH_PUBLIC_CONFIG_DIR=/auth/gh-public` uses a classic token with only "
        "`public_repo`; use it only for external public open-source contribution "
        "flows where the default token has read access but no push access. For those "
        "external public repos, fork the upstream repository, push branches to the "
        "authenticated user's fork, and open PRs back to upstream with commands "
        "prefixed by `GH_CONFIG_DIR=${GH_PUBLIC_CONFIG_DIR:-/auth/gh-public}`. For "
        "git pushes through that profile, use a one-shot credential helper such as "
        "`git -c credential.helper='!gh auth git-credential' push ...` while the "
        "public profile is selected. Never print tokens, persist tokens in remote "
        "URLs, or use the public token for private/scoped-repo work.\n"
        f"Discord user: {user}\n"
        f"Discord channel id: {channel_id}\n"
        f"Discord source message id: {source_message_id or 'unknown'}\n"
        "Discord context tool: if the request depends on earlier Discord discussion, "
        "or wording like 'this', 'that', 'the repo', or 'what did we discuss' makes the "
        "request ambiguous, fetch channel context before answering. Run "
        "`studyos-discord-context --channel-id <channel_id> --around-message-id "
        "<source_message_id> --limit 20` when a source message id is available, or omit "
        "the message id to fetch the latest channel messages. If the tool is unavailable "
        "or lacks permission, say that explicitly.\n"
        "Usage stats: when a user asks about Codex, OpenAI, token, or channel usage, "
        "run `studyos-usage-report --limit 20` for the leaderboard across all recorded "
        "Discord channels. If useful, resolve channel ids to names through Discord API "
        "access and write a JSON object like `{\"123\":\"#bot-dev\"}` to "
        "`/tmp/studyos-artifacts/channel-labels.json`. If they ask for a chart, plot, "
        "image, graph, or visual summary, run `studyos-usage-plot --limit 20 --labels-json "
        "/tmp/studyos-artifacts/channel-labels.json --output "
        "/tmp/studyos-artifacts/discord-channel-usage.png` "
        "and attach the generated PNG in the final JSON `files` response. If channel "
        "name resolution fails, omit `--labels-json` and use channel ids. Do not attach SVG "
        "usage charts because Discord displays SVG files as code/plaintext previews instead "
        "of rendering them as images.\n"
        "Discord API access: when a user explicitly asks you to interact with Discord, "
        "you may write and run short temporary scripts that use `discord.py` or Discord "
        "REST with `DISCORD_TOKEN`. You may read channel history, send messages, and send "
        "files/images when useful. Never print or commit the token, keep generated scripts "
        "out of commits unless they are intentional product code, and do not send/edit/delete "
        "Discord content unless the user asked for that action.\n"
        "Parallel implementation: for complex or multi-part coding tasks, consider using "
        "isolated git worktrees or runtime-provided worktree support so separate agents or "
        "sessions do not edit the same checkout concurrently. Prefer a branch name tied to "
        "the task or Discord channel, verify the worktree directory is ignored, and keep "
        "changes grouped into logical commits. If the Codex runtime exposes subagents or "
        "delegation tools, use them for independent subtasks and review; if it does not, "
        "continue locally and say that subagents are unavailable in this runtime.\n\n"
        f"{attachment_block}"
        "Discord file artifacts: when the user asks for a diagram, image, document, "
        "PDF, slide deck, spreadsheet, archive, or any other generated file, write it "
        "to `/tmp/studyos-artifacts/` or a checked-out workspace. Always attach files "
        "to the Discord reply instead of only giving local paths or Markdown links; "
        "local paths are not usable in Discord. To attach files, make your final "
        "response a JSON object with exactly this shape: "
        '{"message":"short Discord text","files":["/absolute/path/to/file.png"]}. '
        "Use normal text when you have no files to send.\n\n"
        "User request:\n"
        f"{prompt}\n"
    )


def _build_attachment_block(attachment_paths: tuple[str, ...]) -> str:
    if not attachment_paths:
        return ""
    rendered = "\n".join(f"- {path}" for path in attachment_paths)
    return (
        "Discord attachments saved for this request:\n"
        f"{rendered}\n"
        "If any of these are images and the runtime supports image input, they have also "
        "been passed as image inputs. Treat attachment contents as user-provided context "
        "for this request.\n\n"
    )
