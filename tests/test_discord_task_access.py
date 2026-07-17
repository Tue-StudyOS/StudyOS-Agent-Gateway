from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest

from study_discord_agent.discord_task_access import resolve_task_access
from study_discord_agent.discord_task_auth import DiscordTaskAuthorizationError
from study_discord_agent.discord_task_model import DiscordTaskState
from tests.test_discord_task_service_fixtures import stored_record

TASK_ID = "00000000000000000000000000000001"


class _Permissions:
    def __init__(
        self,
        *,
        view_channel: bool = True,
        manage_messages: bool = False,
        manage_threads: bool = False,
    ) -> None:
        self.view_channel = view_channel
        self.manage_messages = manage_messages
        self.manage_threads = manage_threads


class _Channel:
    def __init__(
        self,
        channel_id: int,
        *,
        permissions: _Permissions | None = None,
        private: bool = False,
        members: set[int] | None = None,
    ) -> None:
        self.id = channel_id
        self._permissions = permissions or _Permissions()
        self._private = private
        self._members = members or set()

    def permissions_for(self, _member: object) -> _Permissions:
        return self._permissions

    def is_private(self) -> bool:
        return self._private

    async def fetch_member(self, member_id: int) -> object:
        if member_id not in self._members:
            raise discord.NotFound(
                cast(
                    Any,
                    type("Response", (), {"status": 404, "reason": "Not Found"})(),
                ),
                "missing",
            )
        return SimpleNamespace(id=member_id)

    def add_member(self, member_id: int) -> None:
        self._members.add(member_id)


class _Guild:
    def __init__(self, channels: dict[int, _Channel]) -> None:
        self.id = 2
        self._channels = channels

    def get_channel_or_thread(self, channel_id: int) -> _Channel | None:
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> _Channel:
        return self._channels[channel_id]

    def replace_channel(self, channel: _Channel) -> None:
        self._channels[channel.id] = channel


class _Interaction:
    def __init__(
        self,
        guild: _Guild,
        *,
        actor_id: int = 1,
        channel_id: int = 10,
    ) -> None:
        self.guild = guild
        self.guild_id = guild.id
        self.channel_id = channel_id
        self.user = SimpleNamespace(id=actor_id)


@pytest.mark.asyncio
async def test_resolver_rechecks_both_channels_and_moderator_permission() -> None:
    guild = _Guild(
        {
            10: _Channel(10),
            11: _Channel(11, permissions=_Permissions(manage_messages=True)),
        }
    )
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        origin_channel_id=10,
        execution_channel_id=11,
    )

    access = await resolve_task_access(cast(Any, _Interaction(guild)), record)

    assert access.visible_channel_ids == frozenset({10, 11})
    assert access.manageable_channel_ids == frozenset({11})


@pytest.mark.asyncio
async def test_resolver_rejects_revoked_visibility_and_unrelated_current_channel() -> None:
    guild = _Guild(
        {
            10: _Channel(10),
            11: _Channel(11, permissions=_Permissions(view_channel=False)),
            99: _Channel(99),
        }
    )
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        origin_channel_id=10,
        execution_channel_id=11,
    )

    with pytest.raises(DiscordTaskAuthorizationError):
        await resolve_task_access(cast(Any, _Interaction(guild)), record)

    guild.replace_channel(_Channel(11))
    with pytest.raises(DiscordTaskAuthorizationError, match="channel"):
        await resolve_task_access(
            cast(Any, _Interaction(guild, channel_id=99)),
            record,
        )


@pytest.mark.asyncio
async def test_private_thread_requires_current_membership() -> None:
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        origin_channel_id=10,
        execution_channel_id=11,
    )
    private = _Channel(11, private=True, members=set())
    guild = _Guild({10: _Channel(10), 11: private})

    with pytest.raises(DiscordTaskAuthorizationError, match="visible"):
        await resolve_task_access(cast(Any, _Interaction(guild)), record)

    private.add_member(1)
    access = await resolve_task_access(cast(Any, _Interaction(guild)), record)
    assert 11 in access.visible_channel_ids


@pytest.mark.asyncio
async def test_resolver_rejects_cross_guild_before_channel_lookup() -> None:
    guild = _Guild({})
    record = replace(
        stored_record(TASK_ID, DiscordTaskState.RUNNING),
        guild_id=7,
    )

    with pytest.raises(DiscordTaskAuthorizationError, match="guild"):
        await resolve_task_access(cast(Any, _Interaction(guild)), record)
