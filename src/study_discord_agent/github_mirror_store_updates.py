from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from study_discord_agent.github_mirror_model import (
    MAX_HANDLED_CLAIMS,
    MAX_RECENT_DELIVERIES,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
    GitHubMirrorRecord,
)


def record_from_event(
    event: GitHubMirrorEvent, guild_id: int, channel_id: int, timestamp: str
) -> GitHubMirrorRecord:
    return GitHubMirrorRecord(
        mirror_id=uuid4().hex,
        revision=0,
        guild_id=guild_id,
        channel_id=channel_id,
        card_message_id=None,
        card_create_pending=False,
        card_create_nonce=None,
        card_cleanup_nonce=None,
        publication_pending=True,
        thread_id=None,
        repository_full_name=event.repository_full_name,
        item_kind=event.item_kind,
        item_number=event.item_number,
        item_url=event.item_url,
        title=event.title,
        state=event.state,
        author_login=event.author_login,
        labels=event.labels,
        base_ref=event.base_ref,
        head_ref=event.head_ref,
        base_sha=event.base_sha,
        head_sha=event.head_sha,
        activity=event.activity,
        item_updated_at=event.item_updated_at,
        recent_delivery_ids=(event.delivery_id,),
        pending_action=None,
        handled_interaction_claims=(),
        active_task_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


def update_from_event(
    record: GitHubMirrorRecord, event: GitHubMirrorEvent, timestamp: str
) -> GitHubMirrorRecord:
    deliveries = (*record.recent_delivery_ids, event.delivery_id)[-MAX_RECENT_DELIVERIES:]
    changes: dict[str, object] = {
        "recent_delivery_ids": deliveries,
        "publication_pending": True,
        "revision": record.revision + 1,
        "updated_at": timestamp,
    }
    if _should_apply_event(record, event):
        state = event.state
        if event.event_name == "issue_comment" and event.item_kind is GitHubItemKind.PULL_REQUEST:
            if record.state is GitHubItemState.MERGED:
                state = GitHubItemState.MERGED
            elif record.state is GitHubItemState.DRAFT and event.state is GitHubItemState.OPEN:
                state = GitHubItemState.DRAFT
        changes.update(
            title=event.title,
            state=state,
            author_login=event.author_login,
            labels=event.labels,
            base_ref=event.base_ref if event.base_ref is not None else record.base_ref,
            head_ref=event.head_ref if event.head_ref is not None else record.head_ref,
            base_sha=event.base_sha if event.base_sha is not None else record.base_sha,
            head_sha=event.head_sha if event.head_sha is not None else record.head_sha,
            activity=event.activity,
            item_updated_at=event.item_updated_at,
        )
    return replace(record, **changes)


def updated_record(
    current: GitHubMirrorRecord,
    update: Callable[[GitHubMirrorRecord], GitHubMirrorRecord],
    timestamp: str,
) -> GitHubMirrorRecord:
    candidate = update(current)
    _validate_update(current, candidate)
    return replace(
        candidate,
        revision=current.revision + 1,
        handled_interaction_claims=candidate.handled_interaction_claims[-MAX_HANDLED_CLAIMS:],
        updated_at=timestamp,
    )


def claim_card_record(
    current: GitHubMirrorRecord, creation_nonce: str, timestamp: str
) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(
            record,
            card_create_pending=True,
            card_create_nonce=creation_nonce,
        ),
        timestamp,
    )


def attach_card_record(
    current: GitHubMirrorRecord,
    message_id: int,
    creation_nonce: str,
    timestamp: str,
) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(
            record,
            card_message_id=message_id,
            card_create_pending=False,
            card_create_nonce=None,
            card_cleanup_nonce=creation_nonce,
        ),
        timestamp,
    )


def queue_card_cleanup_record(
    current: GitHubMirrorRecord, creation_nonce: str, timestamp: str
) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(record, card_cleanup_nonce=creation_nonce),
        timestamp,
    )


def release_card_creation_record(current: GitHubMirrorRecord, timestamp: str) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(
            record,
            card_create_pending=False,
            card_create_nonce=None,
        ),
        timestamp,
    )


def complete_card_cleanup_record(current: GitHubMirrorRecord, timestamp: str) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(record, card_cleanup_nonce=None),
        timestamp,
    )


def clear_card_record(current: GitHubMirrorRecord, timestamp: str) -> GitHubMirrorRecord:
    return updated_record(
        current,
        lambda record: replace(
            record,
            card_message_id=None,
            card_create_pending=False,
            card_create_nonce=None,
            card_cleanup_nonce=None,
            publication_pending=True,
        ),
        timestamp,
    )


def _validate_update(current: GitHubMirrorRecord, candidate: GitHubMirrorRecord) -> None:
    immutable = (
        "mirror_id",
        "revision",
        "guild_id",
        "channel_id",
        "repository_full_name",
        "item_kind",
        "item_number",
        "item_url",
        "title",
        "state",
        "author_login",
        "labels",
        "base_ref",
        "head_ref",
        "base_sha",
        "head_sha",
        "activity",
        "item_updated_at",
        "created_at",
        "recent_delivery_ids",
    )
    if any(getattr(current, field) != getattr(candidate, field) for field in immutable):
        raise ValueError("mirror identity and delivery fields cannot change through CAS")


def _should_apply_event(record: GitHubMirrorRecord, event: GitHubMirrorEvent) -> bool:
    event_timestamp = _parse_timestamp(event.item_updated_at)
    record_timestamp = _parse_timestamp(record.item_updated_at)
    if event_timestamp != record_timestamp:
        return event_timestamp > record_timestamp
    terminal_rank = {
        GitHubItemState.DRAFT: 0,
        GitHubItemState.OPEN: 1,
        GitHubItemState.CLOSED: 2,
        GitHubItemState.MERGED: 3,
    }
    return terminal_rank[event.state] >= terminal_rank[record.state]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
