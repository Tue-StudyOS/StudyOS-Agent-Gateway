import discord

from study_discord_agent.github_mirror_model import (
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorRecord,
)

_ACTION_LABELS = (
    (GitHubMirrorAction.REVIEW, "Review", discord.ButtonStyle.primary),
    (GitHubMirrorAction.SECURITY_REVIEW, "Security review", discord.ButtonStyle.secondary),
    (
        GitHubMirrorAction.VULNERABILITY_SCAN,
        "Vulnerability scan",
        discord.ButtonStyle.secondary,
    ),
    (GitHubMirrorAction.WORK, "Work on this", discord.ButtonStyle.success),
)


def github_mirror_view(record: GitHubMirrorRecord) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    children: list[discord.ui.Item[discord.ui.LayoutView]] = [
        discord.ui.TextDisplay[discord.ui.LayoutView](_heading(record)),
        discord.ui.TextDisplay[discord.ui.LayoutView](_details(record)),
        discord.ui.ActionRow[discord.ui.LayoutView](*_buttons(record)),
    ]
    container = discord.ui.Container[discord.ui.LayoutView](
        *children,
        accent_color=_accent_color(record.state),
    )
    view.add_item(container)
    if view.total_children_count > 40 or view.content_length() > 4000:
        raise ValueError("GitHub mirror card exceeds Discord Components V2 bounds")
    return view


def github_mirror_card_signature(record: GitHubMirrorRecord) -> tuple[object, ...]:
    """Fields that can change the rendered Discord card."""
    return (
        record.mirror_id,
        record.repository_full_name,
        record.item_kind,
        record.item_number,
        record.item_url,
        record.title,
        record.state,
        record.author_login,
        record.labels,
        record.base_ref,
        record.head_ref,
        record.activity,
    )


def github_mirror_delivery_marker(nonce: str) -> str:
    return f"-# StudyOS delivery marker: `{nonce}`"


def _heading(record: GitHubMirrorRecord) -> str:
    kind = "Pull request" if record.item_kind is GitHubItemKind.PULL_REQUEST else "Issue"
    return f"### {kind} #{record.item_number}: {_escape(record.title)}"


def _details(record: GitHubMirrorRecord) -> str:
    repository = _escape(record.repository_full_name)
    author = _escape(record.author_login)
    activity = _escape(record.activity)
    lines = [
        f"**{record.state.value.title()}** · `{repository}` · by `@{author}`",
        activity,
    ]
    if record.labels:
        lines.append("Labels: " + ", ".join(f"`{_escape(label)}`" for label in record.labels))
    if record.item_kind is GitHubItemKind.PULL_REQUEST and record.base_ref and record.head_ref:
        lines.append(f"`{_escape(record.head_ref)}` → `{_escape(record.base_ref)}`")
    delivery_nonce = record.card_create_nonce or record.card_cleanup_nonce
    if delivery_nonce is not None:
        lines.append(github_mirror_delivery_marker(delivery_nonce))
    return "\n".join(lines)


def _buttons(
    record: GitHubMirrorRecord,
) -> tuple[discord.ui.Button[discord.ui.LayoutView], ...]:
    buttons: list[discord.ui.Button[discord.ui.LayoutView]] = [
        discord.ui.Button[discord.ui.LayoutView](
            label="Open on GitHub",
            style=discord.ButtonStyle.link,
            url=record.item_url,
        )
    ]
    if record.state in {GitHubItemState.OPEN, GitHubItemState.DRAFT}:
        buttons.extend(
            discord.ui.Button[discord.ui.LayoutView](
                label=label,
                style=style,
                custom_id=f"studyos:github:{action.value}:{record.mirror_id}",
            )
            for action, label, style in _ACTION_LABELS
        )
    return tuple(buttons)


def _escape(value: str) -> str:
    escaped = discord.utils.escape_markdown(value, as_needed=False)
    return discord.utils.escape_mentions(escaped)


def _accent_color(state: GitHubItemState) -> discord.Color:
    if state is GitHubItemState.MERGED:
        return discord.Color.from_rgb(130, 80, 223)
    if state is GitHubItemState.CLOSED:
        return discord.Color.red()
    if state is GitHubItemState.DRAFT:
        return discord.Color.light_grey()
    return discord.Color.green()
