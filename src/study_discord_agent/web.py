import asyncio
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException, Request

from study_discord_agent.config import Settings
from study_discord_agent.github_events import DiscordNotification, notification_from_github_event
from study_discord_agent.security import verify_github_signature


def create_app(
    settings: Settings,
    queue: "asyncio.Queue[DiscordNotification]",
) -> FastAPI:
    app = FastAPI(title="StudyOS Agent Gateway")

    @app.get("/health")
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, str]:
        if not x_github_event:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

        body = await request.body()
        if not verify_github_signature(
            settings.webhook_secret_value,
            body,
            x_hub_signature_256,
        ):
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")

        payload = cast(object, await request.json())
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Webhook payload must be an object")

        notification = _notification_from_payload(x_github_event, cast(dict[str, Any], payload))
        if notification:
            await queue.put(notification)
            return {"status": "queued"}
        return {"status": "ignored"}

    return app


def _notification_from_payload(
    event_name: str,
    payload: dict[str, Any],
) -> DiscordNotification | None:
    try:
        return notification_from_github_event(event_name, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
