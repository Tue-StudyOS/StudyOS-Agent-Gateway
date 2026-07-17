from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import discord

from study_discord_agent.discord_task_controller import DiscordTaskController
from study_discord_agent.discord_task_model import DiscordTaskRecord, DiscordTaskState
from study_discord_agent.discord_task_request import DiscordTaskRequest
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"


class FakeService:
    def __init__(self, records: tuple[DiscordTaskRecord, ...] = ()) -> None:
        self.records = {record.task_id: record for record in records}
        self.starts: list[DiscordTaskRequest] = []
        self.forgotten: list[str] = []
        self.start_error: BaseException | None = None

    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord:
        self.starts.append(request)
        if self.start_error is not None:
            raise self.start_error
        record = stored_record(TASK_ID, DiscordTaskState.STARTING)
        self.records[record.task_id] = record
        return record

    def status(self, task_id: str, _access: object) -> DiscordTaskRecord:
        return self.records[task_id]

    async def forget(self, task_id: str, _access: object, _interaction_id: int) -> None:
        self.forgotten.append(task_id)


class FakeStore:
    def __init__(self, records: tuple[DiscordTaskRecord, ...] = ()) -> None:
        self.items = {record.task_id: record for record in records}

    def get(self, task_id: str) -> DiscordTaskRecord:
        return self.items[task_id]

    def records(self) -> tuple[DiscordTaskRecord, ...]:
        return tuple(self.items.values())


class FakePermissions:
    def __init__(
        self,
        *,
        create_public_threads: bool = True,
        send_messages_in_threads: bool = True,
    ) -> None:
        self.view_channel = True
        self.manage_messages = False
        self.manage_threads = False
        self.create_public_threads = create_public_threads
        self.send_messages_in_threads = send_messages_in_threads


class FakeThread:
    def __init__(self, thread_id: int = 44, parent_id: int = 10) -> None:
        self.id = thread_id
        self.name = "studyos-task"
        self.parent_id = parent_id
        self.category_id = None
        self.deleted = False

    def permissions_for(self, _member: object) -> FakePermissions:
        return FakePermissions()

    def is_private(self) -> bool:
        return False

    async def fetch_member(self, member_id: int) -> object:
        return SimpleNamespace(id=member_id)

    async def delete(self, *, reason: str | None = None) -> None:
        assert reason
        self.deleted = True


class FakeChannel:
    def __init__(
        self,
        *,
        channel_id: int = 10,
        supports_threads: bool = True,
        actor_permissions: FakePermissions | None = None,
        bot_permissions: FakePermissions | None = None,
    ) -> None:
        self.id = channel_id
        self.name = "general"
        self.type = (
            discord.ChannelType.text if supports_threads else discord.ChannelType.voice
        )
        self.created_names: list[str] = []
        self.thread = FakeThread(parent_id=channel_id)
        self.actor_permissions = actor_permissions or FakePermissions()
        self.bot_permissions = bot_permissions or FakePermissions()

    def permissions_for(self, member: object) -> FakePermissions:
        return (
            self.bot_permissions
            if getattr(member, "id", None) == 999
            else self.actor_permissions
        )

    async def create_thread(self, *, name: str, **_: object) -> FakeThread:
        self.created_names.append(name)
        return self.thread


class FakeGuild:
    def __init__(self, channel: FakeChannel) -> None:
        self.id = 2
        self.me = SimpleNamespace(id=999)
        self.channel = channel
        self.fetch_calls: list[int] = []

    def get_channel_or_thread(self, channel_id: int) -> object | None:
        if channel_id == self.channel.id:
            return self.channel
        if channel_id == self.channel.thread.id:
            return self.channel.thread
        return None

    async def fetch_channel(self, channel_id: int) -> object:
        self.fetch_calls.append(channel_id)
        channel = self.get_channel_or_thread(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        return channel


class FakeResponse:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.modal: discord.ui.Modal | None = None
        self.messages: list[dict[str, object]] = []

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        assert ephemeral and thinking
        self.events.append("defer")

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.events.append("modal")
        self.modal = modal

    async def send_message(self, content: str, **kwargs: object) -> None:
        self.events.append("message")
        self.messages.append({"content": content, **kwargs})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str, **kwargs: object) -> None:
        self.messages.append({"content": content, **kwargs})


class FakeInteraction:
    def __init__(self, channel: FakeChannel, *, interaction_id: int = 900) -> None:
        self.id = interaction_id
        self.guild = FakeGuild(channel)
        self.guild_id = 2
        self.channel = channel
        self.channel_id = channel.id
        self.user = SimpleNamespace(id=1)
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def create_controller(
    tmp_path: Path,
    records: tuple[DiscordTaskRecord, ...] = (),
) -> tuple[DiscordTaskController, FakeService, FakeStore]:
    service = FakeService(records)
    store = FakeStore(records)
    controller = DiscordTaskController(
        store=store,
        service=cast(Any, service),
        attachment_root=tmp_path,
    )
    return controller, service, store
