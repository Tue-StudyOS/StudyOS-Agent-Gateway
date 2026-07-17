import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import cast

from study_discord_agent.github_mirror_model import (
    MAX_HANDLED_CLAIMS,
    GitHubHandledActionClaim,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorAction,
    GitHubMirrorRecord,
    GitHubPendingAction,
)

STORE_VERSION = 1


def encode_document(records: Mapping[str, GitHubMirrorRecord]) -> str:
    mirrors = {mirror_id: _encode_record(record) for mirror_id, record in records.items()}
    return json.dumps({"version": STORE_VERSION, "mirrors": mirrors}, sort_keys=True) + "\n"


def decode_document(document: str) -> dict[str, GitHubMirrorRecord]:
    raw: object = json.loads(document, object_pairs_hook=_unique_object)
    data = _mapping(raw, "document")
    _exact_keys(data, {"version", "mirrors"}, "document")
    if _integer(data, "version") != STORE_VERSION:
        raise ValueError("unsupported version")
    mirrors = _mapping(data["mirrors"], "mirrors")
    records = {mirror_id: _decode_record(value) for mirror_id, value in mirrors.items()}
    if any(mirror_id != record.mirror_id for mirror_id, record in records.items()):
        raise ValueError("mirror key does not match record")
    if len({record.logical_key for record in records.values()}) != len(records):
        raise ValueError("duplicate logical mirror")
    return records


def _encode_record(record: GitHubMirrorRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["item_kind"] = record.item_kind.value
    payload["state"] = record.state.value
    payload["labels"] = list(record.labels)
    payload["recent_delivery_ids"] = list(record.recent_delivery_ids)
    payload["handled_interaction_claims"] = [
        {**asdict(claim), "action": claim.action.value}
        for claim in record.handled_interaction_claims
    ]
    if record.pending_action is not None:
        payload["pending_action"] = {
            **asdict(record.pending_action),
            "action": record.pending_action.action.value,
        }
    return cast(dict[str, object], payload)


def _decode_record(raw: object) -> GitHubMirrorRecord:
    data = _mapping(raw, "record")
    _exact_keys(data, set(GitHubMirrorRecord.__dataclass_fields__), "record")
    pending_raw = data["pending_action"]
    pending = None if pending_raw is None else _decode_pending(pending_raw)
    claims_raw = _list(data, "handled_interaction_claims")
    if len(claims_raw) > MAX_HANDLED_CLAIMS:
        raise ValueError("too many handled interaction claims")
    return GitHubMirrorRecord(
        mirror_id=_string(data, "mirror_id"),
        revision=_integer(data, "revision"),
        guild_id=_integer(data, "guild_id"),
        channel_id=_integer(data, "channel_id"),
        card_message_id=_optional_integer(data, "card_message_id"),
        card_create_pending=_boolean(data, "card_create_pending"),
        card_create_nonce=_optional_string(data, "card_create_nonce"),
        card_cleanup_nonce=_optional_string(data, "card_cleanup_nonce"),
        thread_id=_optional_integer(data, "thread_id"),
        repository_full_name=_string(data, "repository_full_name"),
        item_kind=GitHubItemKind(_string(data, "item_kind")),
        item_number=_integer(data, "item_number"),
        item_url=_string(data, "item_url"),
        title=_string(data, "title"),
        state=GitHubItemState(_string(data, "state")),
        author_login=_string(data, "author_login"),
        labels=tuple(_strings(data, "labels")),
        base_ref=_optional_string(data, "base_ref"),
        head_ref=_optional_string(data, "head_ref"),
        base_sha=_optional_string(data, "base_sha"),
        head_sha=_optional_string(data, "head_sha"),
        activity=_string(data, "activity"),
        item_updated_at=_string(data, "item_updated_at"),
        recent_delivery_ids=tuple(_strings(data, "recent_delivery_ids")),
        pending_action=pending,
        handled_interaction_claims=tuple(_decode_claim(value) for value in claims_raw),
        active_task_id=_optional_string(data, "active_task_id"),
        created_at=_string(data, "created_at"),
        updated_at=_string(data, "updated_at"),
    )


def _decode_pending(raw: object) -> GitHubPendingAction:
    data = _mapping(raw, "pending_action")
    _exact_keys(data, set(GitHubPendingAction.__dataclass_fields__), "pending_action")
    return GitHubPendingAction(
        interaction_id=_integer(data, "interaction_id"),
        action=GitHubMirrorAction(_string(data, "action")),
        task_id=_string(data, "task_id"),
        claimed_at=_string(data, "claimed_at"),
    )


def _decode_claim(raw: object) -> GitHubHandledActionClaim:
    data = _mapping(raw, "handled claim")
    _exact_keys(data, set(GitHubHandledActionClaim.__dataclass_fields__), "handled claim")
    succeeded = data["succeeded"]
    if type(succeeded) is not bool:
        raise ValueError("succeeded must be a boolean")
    return GitHubHandledActionClaim(
        interaction_id=_integer(data, "interaction_id"),
        action=GitHubMirrorAction(_string(data, "action")),
        task_id=_string(data, "task_id"),
        succeeded=succeeded,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _mapping(raw: object, name: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object")
    data = cast(dict[object, object], raw)
    if not all(isinstance(key, str) for key in data):
        raise ValueError(f"{name} keys must be strings")
    return {cast(str, key): value for key, value in data.items()}


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data[key]
    if value is None:
        return None
    return _string(data, key)


def _integer(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(data: Mapping[str, object], key: str) -> bool:
    value = data[key]
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_integer(data: Mapping[str, object], key: str) -> int | None:
    value = data[key]
    if value is None:
        return None
    return _integer(data, key)


def _list(data: Mapping[str, object], key: str) -> list[object]:
    value = data[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return cast(list[object], value)


def _strings(data: Mapping[str, object], key: str) -> list[str]:
    values = _list(data, key)
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"{key} entries must be strings")
    return cast(list[str], values)


def _exact_keys(data: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(data) != expected:
        raise ValueError(f"{name} has unexpected fields")
