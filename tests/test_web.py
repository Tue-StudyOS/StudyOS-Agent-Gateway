import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from study_discord_agent.config import Settings
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.web import MAX_GITHUB_WEBHOOK_BYTES, create_app

SECRET = "webhook-secret"


def _payload() -> dict[str, object]:
    return {
        "action": "opened",
        "issue": {
            "number": 12,
            "title": "Question",
            "html_url": "https://github.com/Tue-StudyOS/example/issues/12",
            "state": "open",
            "updated_at": "2026-07-17T12:00:00Z",
            "user": {"login": "student"},
            "labels": [],
        },
        "repository": {"full_name": "Tue-StudyOS/example"},
        "sender": {"login": "actor"},
    }


def _settings(*, channel_id: int | None = 20) -> Settings:
    return Settings(
        discord_token=SecretStr("discord"),
        discord_guild_id=10,
        discord_pr_channel_id=channel_id,
        github_webhook_secret=SecretStr(SECRET),
    )


def _headers(body: bytes, *, delivery: str | None = "delivery-web") -> dict[str, str]:
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json",
    }
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    return headers


async def _post(app: object, body: bytes, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/webhooks/github", content=body, headers=headers)


def _store(tmp_path: Path) -> GitHubMirrorStore:
    return GitHubMirrorStore(tmp_path / "mirrors.json")


@pytest.mark.asyncio
async def test_valid_signed_delivery_is_durable_before_acknowledgement(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    store = _store(tmp_path)
    app = create_app(_settings(), queue, store)
    body = json.dumps(_payload()).encode()

    response = await _post(app, body, _headers(body))

    assert response.status_code == 200
    mirror_id = queue.get_nowait()
    record = store.get(mirror_id)
    assert record.recent_delivery_ids == ("delivery-web",)
    assert record.publication_pending
    assert (tmp_path / "mirrors.json").exists()


@pytest.mark.asyncio
async def test_delivery_and_destination_are_required(tmp_path: Path) -> None:
    body = json.dumps(_payload()).encode()
    queue: asyncio.Queue[str] = asyncio.Queue()
    store = _store(tmp_path)

    response = await _post(
        create_app(_settings(), queue, store), body, _headers(body, delivery=None)
    )
    assert response.status_code == 400
    assert "Delivery" in response.json()["detail"]

    response = await _post(
        create_app(_settings(channel_id=None), queue, store), body, _headers(body)
    )
    assert response.status_code == 503
    assert queue.empty()

    response = await _post(
        create_app(_settings(), queue, store), body, _headers(body, delivery=" ")
    )
    assert response.status_code == 400
    assert queue.empty()


@pytest.mark.asyncio
async def test_signature_is_checked_before_json_and_payload_errors_are_generic(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    store = _store(tmp_path)
    app = create_app(_settings(), queue, store)
    malformed = b'{"body":"must not leak"'
    headers = _headers(malformed)
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64

    response = await _post(app, malformed, headers)
    assert response.status_code == 401

    response = await _post(
        create_app(_settings(channel_id=None), queue, store), malformed, headers
    )
    assert response.status_code == 401

    invalid = _payload()
    issue = invalid["issue"]
    assert isinstance(issue, dict)
    issue["html_url"] = "https://evil.example/secret"
    body = json.dumps(invalid).encode()
    response = await _post(app, body, _headers(body))
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid GitHub webhook payload"
    assert queue.empty()


@pytest.mark.asyncio
async def test_oversized_unauthenticated_body_is_rejected_before_hmac(
    tmp_path: Path,
) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    store = _store(tmp_path)
    body = b"x" * (MAX_GITHUB_WEBHOOK_BYTES + 1)
    headers = _headers(body)
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64

    response = await _post(create_app(_settings(), queue, store), body, headers)

    assert response.status_code == 413
    assert queue.empty()
    assert store.records() == ()
