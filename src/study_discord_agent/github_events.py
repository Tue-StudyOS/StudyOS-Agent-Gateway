from typing import cast

from study_discord_agent.github_mirror_model import (
    GITHUB_EVENT_ACTIONS,
    MAX_ACTIVITY_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_LABELS,
    MAX_TITLE_LENGTH,
    GitHubItemKind,
    GitHubItemState,
    GitHubMirrorEvent,
)


def event_from_github_webhook(
    event_name: str, delivery_id: str, payload: dict[str, object]
) -> GitHubMirrorEvent | None:
    action = str(payload.get("action", ""))
    if action not in GITHUB_EVENT_ACTIONS.get(event_name, frozenset()):
        return None
    if event_name == "pull_request":
        return _pull_request_event(delivery_id, action, payload)
    return _issue_event(delivery_id, event_name, action, payload)


def _pull_request_event(
    delivery_id: str, action: str, payload: dict[str, object]
) -> GitHubMirrorEvent:
    item = _object(payload, "pull_request")
    repository = _repository(payload)
    number = _number(item)
    state = _pull_request_state(item)
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name="pull_request",
        action=action,
        repository_full_name=repository,
        item_kind=GitHubItemKind.PULL_REQUEST,
        item_number=number,
        item_url=_canonical_url(item.get("html_url"), repository, "pull", number),
        title=_text(item, "title", MAX_TITLE_LENGTH),
        state=state,
        author_login=_login(item),
        labels=_labels(item),
        base_ref=_ref(item, "base"),
        head_ref=_ref(item, "head"),
        base_sha=_sha(item, "base"),
        head_sha=_sha(item, "head"),
        activity=f"Pull request {_activity(action)}",
        item_updated_at=_timestamp(item, "updated_at"),
    )


def _issue_event(
    delivery_id: str, event_name: str, action: str, payload: dict[str, object]
) -> GitHubMirrorEvent:
    item = _object(payload, "issue")
    repository = _repository(payload)
    number = _number(item)
    marker_raw = item.get("pull_request")
    marker = None if marker_raw is None else _dictionary(marker_raw)
    kind = GitHubItemKind.PULL_REQUEST if marker is not None else GitHubItemKind.ISSUE
    path = "pull" if kind is GitHubItemKind.PULL_REQUEST else "issues"
    url_source = marker.get("html_url") if marker is not None else item.get("html_url")
    state = GitHubItemState.CLOSED if item.get("state") == "closed" else GitHubItemState.OPEN
    activity_subject = "Comment" if event_name == "issue_comment" else "Issue"
    updated_source = _object(payload, "comment") if event_name == "issue_comment" else item
    return GitHubMirrorEvent(
        delivery_id=delivery_id,
        event_name=event_name,
        action=action,
        repository_full_name=repository,
        item_kind=kind,
        item_number=number,
        item_url=_canonical_url(url_source, repository, path, number),
        title=_text(item, "title", MAX_TITLE_LENGTH),
        state=state,
        author_login=_login(item),
        labels=_labels(item),
        base_ref=None,
        head_ref=None,
        base_sha=None,
        head_sha=None,
        activity=f"{activity_subject} {_activity(action)}",
        item_updated_at=_timestamp(updated_source, "updated_at"),
    )


def _object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    try:
        return _dictionary(value)
    except ValueError as error:
        raise ValueError(f"GitHub payload missing object: {key}") from error


def _dictionary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("GitHub payload value must be an object")
    raw = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw):
        raise ValueError("GitHub payload object keys must be strings")
    return {cast(str, key): item for key, item in raw.items()}


def _repository(payload: dict[str, object]) -> str:
    return _text(_object(payload, "repository"), "full_name", 200)


def _number(item: dict[str, object]) -> int:
    value = item.get("number")
    if type(value) is not int or value <= 0:
        raise ValueError("GitHub item number must be a positive integer")
    return value


def _canonical_url(value: object, repository: str, path: str, number: int) -> str:
    expected = f"https://github.com/{repository}/{path}/{number}"
    if value != expected:
        raise ValueError("GitHub item URL must be canonical")
    return expected


def _text(item: dict[str, object], key: str, maximum: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"GitHub {key} exceeds display bounds")
    return value


def _login(item: dict[str, object]) -> str:
    return _text(_object(item, "user"), "login", 39)


def _labels(item: dict[str, object]) -> tuple[str, ...]:
    raw = item.get("labels", [])
    if not isinstance(raw, list):
        raise ValueError("GitHub labels exceed display bounds")
    values = cast(list[object], raw)
    if len(values) > MAX_LABELS:
        raise ValueError("GitHub labels exceed display bounds")
    labels: list[str] = []
    for value in values:
        try:
            label = _dictionary(value)
        except ValueError as error:
            raise ValueError("GitHub label must be an object") from error
        name = _text(label, "name", MAX_LABEL_LENGTH)
        if name not in labels:
            labels.append(name)
    return tuple(labels)


def _ref(item: dict[str, object], side: str) -> str:
    return _text(_object(item, side), "ref", 255)


def _sha(item: dict[str, object], side: str) -> str:
    value = _text(_object(item, side), "sha", 40).lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("GitHub SHA must contain exactly 40 hexadecimal characters")
    return value


def _timestamp(item: dict[str, object], key: str) -> str:
    return _text(item, key, 40)


def _pull_request_state(item: dict[str, object]) -> GitHubItemState:
    if item.get("merged") is True:
        return GitHubItemState.MERGED
    if item.get("state") == "closed":
        return GitHubItemState.CLOSED
    if item.get("draft") is True:
        return GitHubItemState.DRAFT
    return GitHubItemState.OPEN


def _activity(action: str) -> str:
    return action.replace("_", " ")[:MAX_ACTIVITY_LENGTH]
