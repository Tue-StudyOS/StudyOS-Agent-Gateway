import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

MAX_TITLE_LENGTH = 256
MAX_ACTIVITY_LENGTH = 160
MAX_LABELS = 12
MAX_LABEL_LENGTH = 64
MAX_DELIVERY_ID_LENGTH = 128
MAX_RECENT_DELIVERIES = 64
MAX_HANDLED_CLAIMS = 64
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_SHA = re.compile(r"[0-9a-f]{40}")
_OPAQUE_ID = re.compile(r"[0-9a-f]{32}")
_DELIVERY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
GITHUB_EVENT_ACTIONS = {
    "pull_request": frozenset(
        {
            "opened",
            "edited",
            "reopened",
            "ready_for_review",
            "synchronize",
            "labeled",
            "unlabeled",
            "closed",
        }
    ),
    "issues": frozenset({"opened", "edited", "reopened", "labeled", "unlabeled", "closed"}),
    "issue_comment": frozenset({"created", "edited", "deleted"}),
}


class GitHubItemKind(StrEnum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"


class GitHubItemState(StrEnum):
    OPEN = "open"
    DRAFT = "draft"
    CLOSED = "closed"
    MERGED = "merged"


class GitHubMirrorAction(StrEnum):
    REVIEW = "review"
    SECURITY_REVIEW = "security_review"
    VULNERABILITY_SCAN = "vulnerability_scan"
    WORK = "work"


@dataclass(frozen=True)
class GitHubHandledActionClaim:
    interaction_id: int
    action: GitHubMirrorAction
    task_id: str
    succeeded: bool

    def __post_init__(self) -> None:
        _exact_enum(self.action, GitHubMirrorAction, "action")
        _positive_integer(self.interaction_id, "interaction_id")
        _opaque_task_id(self.task_id, "task_id")
        if type(self.succeeded) is not bool:
            raise ValueError("succeeded must be a boolean")


@dataclass(frozen=True)
class GitHubPendingAction:
    interaction_id: int
    action: GitHubMirrorAction
    task_id: str
    claimed_at: str

    def __post_init__(self) -> None:
        _exact_enum(self.action, GitHubMirrorAction, "action")
        _positive_integer(self.interaction_id, "interaction_id")
        _opaque_task_id(self.task_id, "task_id")
        _timestamp(self.claimed_at, "claimed_at")


@dataclass(frozen=True)
class GitHubMirrorEvent:
    delivery_id: str
    event_name: str
    action: str
    repository_full_name: str
    item_kind: GitHubItemKind
    item_number: int
    item_url: str
    title: str
    state: GitHubItemState
    author_login: str
    labels: tuple[str, ...]
    base_ref: str | None
    head_ref: str | None
    base_sha: str | None
    head_sha: str | None
    activity: str
    item_updated_at: str

    def __post_init__(self) -> None:
        _exact_enum(self.item_kind, GitHubItemKind, "item_kind")
        _exact_enum(self.state, GitHubItemState, "state")
        if not _DELIVERY_ID.fullmatch(self.delivery_id):
            raise ValueError("delivery_id must be a bounded identifier")
        if self.action not in GITHUB_EVENT_ACTIONS.get(self.event_name, frozenset()):
            raise ValueError("event_name and action are not supported")
        _item_identity(self.repository_full_name, self.item_kind, self.item_number, self.item_url)
        _bounded_text(self.title, "title", MAX_TITLE_LENGTH)
        if not _LOGIN.fullmatch(self.author_login):
            raise ValueError("author_login is not a valid GitHub login")
        _labels(self.labels)
        _references(self.item_kind, self.base_ref, self.head_ref, self.base_sha, self.head_sha)
        _bounded_text(self.activity, "activity", MAX_ACTIVITY_LENGTH)
        _timestamp(self.item_updated_at, "item_updated_at")

    @property
    def agent_prompt(self) -> None:
        """Compatibility sentinel: webhook events are always passive."""
        return None


@dataclass(frozen=True)
class GitHubMirrorRecord:
    mirror_id: str
    revision: int
    guild_id: int
    channel_id: int
    card_message_id: int | None
    card_create_pending: bool
    thread_id: int | None
    repository_full_name: str
    item_kind: GitHubItemKind
    item_number: int
    item_url: str
    title: str
    state: GitHubItemState
    author_login: str
    labels: tuple[str, ...]
    base_ref: str | None
    head_ref: str | None
    base_sha: str | None
    head_sha: str | None
    activity: str
    item_updated_at: str
    recent_delivery_ids: tuple[str, ...]
    pending_action: GitHubPendingAction | None
    handled_interaction_claims: tuple[GitHubHandledActionClaim, ...]
    active_task_id: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _exact_enum(self.item_kind, GitHubItemKind, "item_kind")
        _exact_enum(self.state, GitHubItemState, "state")
        if not _OPAQUE_ID.fullmatch(self.mirror_id):
            raise ValueError("mirror_id must be opaque lowercase UUID hex")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        _positive_integer(self.guild_id, "guild_id")
        _positive_integer(self.channel_id, "channel_id")
        _optional_positive_integer(self.card_message_id, "card_message_id")
        if type(self.card_create_pending) is not bool:
            raise ValueError("card_create_pending must be a boolean")
        if self.card_message_id is not None and self.card_create_pending:
            raise ValueError("an attached card cannot have pending creation")
        _optional_positive_integer(self.thread_id, "thread_id")
        _item_identity(self.repository_full_name, self.item_kind, self.item_number, self.item_url)
        _bounded_text(self.title, "title", MAX_TITLE_LENGTH)
        if not _LOGIN.fullmatch(self.author_login):
            raise ValueError("author_login is not a valid GitHub login")
        _labels(self.labels)
        _references(self.item_kind, self.base_ref, self.head_ref, self.base_sha, self.head_sha)
        _bounded_text(self.activity, "activity", MAX_ACTIVITY_LENGTH)
        _timestamp(self.item_updated_at, "item_updated_at")
        for delivery_id in self.recent_delivery_ids:
            if not _DELIVERY_ID.fullmatch(delivery_id):
                raise ValueError("delivery_id must be a bounded identifier")
        if len(self.recent_delivery_ids) > MAX_RECENT_DELIVERIES:
            raise ValueError("too many recent delivery IDs")
        if len(set(self.recent_delivery_ids)) != len(self.recent_delivery_ids):
            raise ValueError("recent delivery IDs must be unique")
        claim_ids = [claim.interaction_id for claim in self.handled_interaction_claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("handled interaction claims must be unique")
        if self.active_task_id is not None:
            _opaque_task_id(self.active_task_id, "active_task_id")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.updated_at, "updated_at")

    @property
    def logical_key(self) -> tuple[str, GitHubItemKind, int]:
        return self.repository_full_name, self.item_kind, self.item_number


def _item_identity(repository: str, kind: GitHubItemKind, number: int, url: str) -> None:
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository_full_name is invalid")
    _positive_integer(number, "item_number")
    item_path = "pull" if kind is GitHubItemKind.PULL_REQUEST else "issues"
    expected_url = f"https://github.com/{repository}/{item_path}/{number}"
    parsed = urlsplit(url)
    if url != expected_url or parsed.query or parsed.fragment:
        raise ValueError("item URL is not the canonical GitHub URL")


def _references(
    kind: GitHubItemKind,
    base_ref: str | None,
    head_ref: str | None,
    base_sha: str | None,
    head_sha: str | None,
) -> None:
    if kind is GitHubItemKind.ISSUE and any(
        value is not None for value in (base_ref, head_ref, base_sha, head_sha)
    ):
        raise ValueError("issues cannot carry pull request revisions")
    _optional_bounded_text(base_ref, "base_ref", 255)
    _optional_bounded_text(head_ref, "head_ref", 255)
    for name, value in (("base_sha", base_sha), ("head_sha", head_sha)):
        if value is not None and not _SHA.fullmatch(value):
            raise ValueError(f"{name} must be a lowercase 40-character SHA")


def _labels(labels: tuple[str, ...]) -> None:
    if len(labels) > MAX_LABELS:
        raise ValueError("too many labels")
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")
    for label in labels:
        _bounded_text(label, "label", MAX_LABEL_LENGTH)


def _timestamp(value: str, name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value <= 0 or value > (2**64 - 1):
        raise ValueError(f"{name} must be a positive integer")


def _exact_enum(value: object, expected: type[StrEnum], name: str) -> None:
    if type(value) is not expected:
        raise ValueError(f"{name} must use the declared enum type")


def _optional_positive_integer(value: int | None, name: str) -> None:
    if value is not None:
        _positive_integer(value, name)


def _bounded_text(value: str, name: str, maximum: int) -> None:
    if not value or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")


def _optional_bounded_text(value: str | None, name: str, maximum: int) -> None:
    if value is not None:
        _bounded_text(value, name, maximum)


def _opaque_task_id(value: str, name: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an opaque UUID") from error
    if str(parsed) != value:
        raise ValueError(f"{name} must be an opaque UUID")
