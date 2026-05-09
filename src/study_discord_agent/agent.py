import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, cast

import httpx


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
        if self._webhook_url:
            return await self._ask_webhook(prompt, user, channel_id)
        if self._command:
            return await self._ask_command(prompt, user, channel_id)
        raise RuntimeError("Configure AGENT_WEBHOOK_URL or AGENT_COMMAND")

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

        full_prompt = (
            "You are running from a Discord/GitHub course collaboration bot.\n"
            f"Discord user: {user}\n"
            f"Discord channel id: {channel_id}\n\n"
            f"{prompt}\n"
        )
        process = await asyncio.create_subprocess_exec(
            *shlex.split(self._command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workdir,
        )
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
            raise RuntimeError(f"Agent command failed: {error[:1000]}")

        message = stdout.decode("utf-8", errors="replace").strip()
        if not message:
            raise RuntimeError("Agent command produced no output")
        return AgentReply(message=message)
