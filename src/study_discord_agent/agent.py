import asyncio
import json
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx

from study_discord_agent.prompt_context import build_agent_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentReply:
    message: str


class AgentGateway:
    def __init__(
        self,
        webhook_url: str | None,
        command: str | None,
        workdir: str | None,
        timeout_seconds: int,
    ) -> None:
        self._webhook_url = webhook_url
        self._command = command
        self._workdir = workdir
        self._timeout_seconds = timeout_seconds

    async def ask(self, prompt: str, user: str, channel_id: int) -> AgentReply:
        started_at = time.monotonic()
        logger.info("agent request started source_user=%s channel_id=%s", user, channel_id)
        if self._webhook_url:
            reply = await self._ask_webhook(prompt, user, channel_id)
        elif self._command:
            reply = await self._ask_command(prompt, user, channel_id)
        else:
            raise RuntimeError("Configure AGENT_WEBHOOK_URL or AGENT_COMMAND")

        elapsed = time.monotonic() - started_at
        logger.info(
            "agent request completed source_user=%s channel_id=%s elapsed_seconds=%.2f",
            user,
            channel_id,
            elapsed,
        )
        return reply

    async def _ask_webhook(self, prompt: str, user: str, channel_id: int) -> AgentReply:
        if not self._webhook_url:
            raise RuntimeError("AGENT_WEBHOOK_URL is not configured")

        payload = {
            "prompt": prompt,
            "source": "discord",
            "user": user,
            "channel_id": channel_id,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(self._webhook_url, json=payload)
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())

        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RuntimeError("Agent response must contain a non-empty message")
        return AgentReply(message=message)

    async def _ask_command(self, prompt: str, user: str, channel_id: int) -> AgentReply:
        if not self._command:
            raise RuntimeError("AGENT_COMMAND is not configured")

        full_prompt = build_agent_prompt(prompt, user, channel_id, os.environ.get("CODEX_HOME"))
        process = await asyncio.create_subprocess_exec(
            *shlex.split(self._command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workdir,
        )
        logger.info("agent command spawned pid=%s command=%s", process.pid, self._command)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(full_prompt.encode("utf-8")),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Agent command timed out") from None

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("agent command failed returncode=%s error=%s", process.returncode, error)
            raise RuntimeError(f"Agent command failed: {error[:1000]}")

        output = stdout.decode("utf-8", errors="replace").strip()
        message = _extract_agent_message(output)
        if not message:
            raise RuntimeError("Agent command produced no output")
        return AgentReply(message=message)


def _extract_agent_message(output: str) -> str:
    """Return the final assistant message from Codex JSONL output, or raw output."""
    messages: list[str] = []
    for line in output.splitlines():
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        event = cast(dict[str, object], parsed)
        item_obj = event.get("item")
        if not isinstance(item_obj, dict):
            continue
        item = cast(dict[str, object], item_obj)
        text = item.get("text")
        if item.get("type") == "agent_message" and isinstance(text, str):
            messages.append(text)

    if messages:
        return messages[-1].strip()
    return output
