import asyncio
from typing import cast

from fastapi import FastAPI, Header, HTTPException, Request

from study_discord_agent.config import Settings
from study_discord_agent.github_events import event_from_github_webhook
from study_discord_agent.github_mirror_model import GitHubMirrorEvent
from study_discord_agent.security import verify_github_signature


def create_app(
    settings: Settings,
    queue: "asyncio.Queue[GitHubMirrorEvent]",
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

        body = await request.body()
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
            payload = cast(object, await request.json())
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
            await queue.put(event)
            return {"status": "queued"}
        return {"status": "ignored"}

    return app


def _event_from_payload(
    event_name: str,
    delivery_id: str,
    payload: dict[str, object],
) -> GitHubMirrorEvent | None:
    try:
        return event_from_github_webhook(event_name, delivery_id, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload") from exc
