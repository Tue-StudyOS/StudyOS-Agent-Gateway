import asyncio
import json
from typing import cast

from fastapi import FastAPI, Header, HTTPException, Request

from study_discord_agent.config import Settings
from study_discord_agent.github_events import event_from_github_webhook
from study_discord_agent.github_mirror_model import GitHubMirrorEvent
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.security import verify_github_signature

MAX_GITHUB_WEBHOOK_BYTES = 1024 * 1024


def create_app(
    settings: Settings,
    queue: "asyncio.Queue[str]",
    mirror_store: GitHubMirrorStore,
) -> FastAPI:
    app = FastAPI(title="StudyOS Agent Gateway")

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, str]:
        if not x_github_event:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")
        if not x_github_delivery:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header")
        if not settings.webhook_secret_value:
            raise HTTPException(status_code=503, detail="GitHub webhooks are not configured")

        body = await _read_limited_body(request)
        if not verify_github_signature(
            settings.webhook_secret_value,
            body,
            x_hub_signature_256,
        ):
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")
        if settings.discord_guild_id is None or settings.discord_pr_channel_id is None:
            raise HTTPException(
                status_code=503, detail="GitHub mirror destination is not configured"
            )

        try:
            payload = cast(object, json.loads(body))
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload")

        event = _event_from_payload(
            x_github_event,
            x_github_delivery,
            cast(dict[str, object], payload),
        )
        if event:
            assert settings.discord_guild_id is not None
            assert settings.discord_pr_channel_id is not None
            staged = mirror_store.upsert_event(
                event,
                guild_id=settings.discord_guild_id,
                channel_id=settings.discord_pr_channel_id,
            )
            if staged.record.publication_pending:
                await queue.put(staged.record.mirror_id)
            return {"status": "queued"}
        return {"status": "ignored"}

    return app


async def _read_limited_body(request: Request) -> bytes:
    body = bytearray()
    stream = request.stream()
    try:
        async for chunk in stream:
            if len(body) + len(chunk) > MAX_GITHUB_WEBHOOK_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="GitHub webhook payload is too large",
                )
            body.extend(chunk)
    finally:
        await stream.aclose()
    return bytes(body)


def _event_from_payload(
    event_name: str,
    delivery_id: str,
    payload: dict[str, object],
) -> GitHubMirrorEvent | None:
    try:
        return event_from_github_webhook(event_name, delivery_id, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload") from exc
