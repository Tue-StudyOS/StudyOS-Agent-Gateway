import asyncio
from pathlib import Path
from typing import cast

from study_discord_agent.agent import (
    AgentChannelCapabilities,
    AgentExecutionContext,
    AgentReply,
    ProgressSink,
)
from study_discord_agent.codex_app_server_runtime import SteerResult
from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_reply_content import PreparedDiscordReply
from study_discord_agent.discord_task_delivery import DiscordTaskPresentation
from study_discord_agent.discord_task_model import DiscordTaskRecord


class FakeAgent:
    def __init__(self) -> None:
        self.ask_calls: list[dict[str, object]] = []
        self.steer_calls: list[dict[str, object]] = []
        self.interrupt_calls: list[int] = []
        self.start_calls = 0
        self.ask_started: dict[int, asyncio.Event] = {}
        self.ask_release: dict[int, asyncio.Event] = {}
        self.ask_errors: dict[int, BaseException] = {}
        self.replies: dict[int, AgentReply] = {}
        self.start_entered = asyncio.Event()
        self.start_release: asyncio.Event | None = None
        self.start_error: BaseException | None = None
        self.steer_result = SteerResult.STEERED
        self.interrupt_result = False
        self.capabilities: dict[int, AgentChannelCapabilities] = {}

    def block_channel(self, channel_id: int) -> asyncio.Event:
        release = asyncio.Event()
        self.ask_release[channel_id] = release
        return release

    async def start(self) -> None:
        self.start_calls += 1
        self.start_entered.set()
        if self.start_release is not None:
            await self.start_release.wait()
        if self.start_error is not None:
            raise self.start_error

    async def ask(
        self,
        prompt: str,
        user: str,
        channel_id: int | None,
        source_message_id: int | None = None,
        attachment_paths: tuple[Path, ...] = (),
        origin_context: DiscordOriginContext | None = None,
        on_progress: ProgressSink | None = None,
        execution: AgentExecutionContext | None = None,
    ) -> AgentReply:
        del on_progress
        assert channel_id is not None
        self.ask_calls.append(
            {
                "prompt": prompt,
                "user": user,
                "channel_id": channel_id,
                "source_message_id": source_message_id,
                "attachment_paths": attachment_paths,
                "origin_context": origin_context,
                "execution": execution,
            }
        )
        self.ask_started.setdefault(channel_id, asyncio.Event()).set()
        if release := self.ask_release.get(channel_id):
            await release.wait()
        if error := self.ask_errors.get(channel_id):
            raise error
        return self.replies.get(channel_id, AgentReply(message=f"done-{channel_id}"))

    async def steer(
        self,
        *,
        prompt: str,
        user: str,
        channel_id: int,
        source_message_id: int | None,
        attachment_paths: tuple[Path, ...] = (),
        origin_context: DiscordOriginContext | None = None,
    ) -> SteerResult:
        self.steer_calls.append(
            {
                "prompt": prompt,
                "user": user,
                "channel_id": channel_id,
                "source_message_id": source_message_id,
                "attachment_paths": attachment_paths,
                "origin_context": origin_context,
            }
        )
        return self.steer_result

    async def interrupt(self, channel_id: int) -> bool:
        self.interrupt_calls.append(channel_id)
        return self.interrupt_result

    async def channel_capabilities(self, channel_id: int) -> AgentChannelCapabilities:
        return self.capabilities.get(
            channel_id,
            AgentChannelCapabilities(False, False, False, False),
        )


class FakePresentation(DiscordTaskPresentation):
    def __init__(self) -> None:
        self.create_calls: list[DiscordTaskRecord] = []
        self.render_calls: list[DiscordTaskRecord] = []
        self.prepare_calls: list[tuple[DiscordTaskRecord, AgentReply]] = []
        self.deliver_calls: list[tuple[DiscordTaskRecord, PreparedDiscordReply]] = []
        self.create_release: dict[int, asyncio.Event] = {}
        self.create_entered: dict[int, asyncio.Event] = {}
        self.missing_card_channels: set[int] = set()
        self.raise_create_channels: set[int] = set()
        self.raise_render = False
        self.prepare_error: BaseException | None = None
        self.prepared_by_channel: dict[int, PreparedDiscordReply] = {}
        self.delivery_outcomes: list[int | BaseException] = []
        self.delivery_entered = asyncio.Event()
        self.delivery_release: asyncio.Event | None = None

    def block_card(self, channel_id: int) -> asyncio.Event:
        release = asyncio.Event()
        self.create_release[channel_id] = release
        return release

    async def create_card(self, record: DiscordTaskRecord) -> int | None:
        self.create_calls.append(record)
        self.create_entered.setdefault(record.execution_channel_id, asyncio.Event()).set()
        if release := self.create_release.get(record.execution_channel_id):
            await release.wait()
        if record.execution_channel_id in self.raise_create_channels:
            raise RuntimeError("card unavailable")
        if record.execution_channel_id in self.missing_card_channels:
            return None
        return 10_000 + len(self.create_calls)

    async def render_card(self, record: DiscordTaskRecord) -> None:
        self.render_calls.append(record)
        if self.raise_render:
            raise RuntimeError("card edit unavailable")

    async def prepare_reply(
        self, record: DiscordTaskRecord, reply: AgentReply
    ) -> PreparedDiscordReply:
        self.prepare_calls.append((record, reply))
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.prepared_by_channel.get(
            record.execution_channel_id,
            PreparedDiscordReply(message=reply.message, files=reply.files),
        )

    async def deliver_reply(
        self, record: DiscordTaskRecord, reply: PreparedDiscordReply
    ) -> int:
        self.deliver_calls.append((record, reply))
        self.delivery_entered.set()
        if self.delivery_release is not None:
            await self.delivery_release.wait()
        outcome = self.delivery_outcomes.pop(0) if self.delivery_outcomes else 20_000
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def progress_sink(self, task_id: str) -> ProgressSink:
        del task_id

        async def sink(_progress: object) -> None:
            return None

        return cast(ProgressSink, sink)
