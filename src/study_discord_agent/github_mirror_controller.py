import logging
from pathlib import Path
from typing import Protocol

import discord

from study_discord_agent.discord_task_model import DiscordTaskRecord
from study_discord_agent.discord_task_request import DiscordTaskRequest
from study_discord_agent.github_mirror_action_store import GitHubMirrorActionUnavailable
from study_discord_agent.github_mirror_discord import (
    GitHubMirrorDiscordError,
    fetch_card_message,
    respond_interaction,
    respond_message,
    validate_modal,
    validated_button,
)
from study_discord_agent.github_mirror_modal import GitHubWorkModal
from study_discord_agent.github_mirror_model import GitHubMirrorAction, GitHubMirrorRecord
from study_discord_agent.github_mirror_store import GitHubMirrorStore
from study_discord_agent.github_mirror_task_starter import GitHubMirrorTaskStarter

logger = logging.getLogger(__name__)


class _TaskStore(Protocol):
    def get(self, task_id: str) -> DiscordTaskRecord: ...


class _TaskService(Protocol):
    async def start(self, request: DiscordTaskRequest) -> DiscordTaskRecord: ...


class GitHubMirrorController:
    def __init__(
        self,
        client: discord.Client,
        mirror_store: GitHubMirrorStore,
        task_store: _TaskStore,
        task_service: _TaskService,
        canonical_root: Path,
    ) -> None:
        self._client = client
        self._mirrors = mirror_store
        self._starter = GitHubMirrorTaskStarter(
            client, mirror_store, task_store, task_service, canonical_root
        )

    async def handle_mirror_action(
        self,
        action: GitHubMirrorAction,
        mirror_id: str,
        interaction: discord.Interaction,
    ) -> None:
        try:
            record = self._mirrors.get(mirror_id)
            card = validated_button(record, interaction)
        except (KeyError, GitHubMirrorDiscordError) as error:
            await respond_interaction(interaction, _public_error(error))
            return
        if action is GitHubMirrorAction.WORK:
            await interaction.response.send_modal(
                GitHubWorkModal(
                    self.submit_work,
                    mirror_id=mirror_id,
                    card_message=card,
                    actor_id=interaction.user.id,
                )
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._interaction_start(action, record, card, interaction, None)

    async def submit_work(
        self,
        mirror_id: str,
        expected_card_id: int,
        expected_actor_id: int,
        instruction: str,
        card_message: discord.Message,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record = self._mirrors.get(mirror_id)
            validate_modal(
                record,
                interaction,
                expected_card_id=expected_card_id,
                expected_actor_id=expected_actor_id,
                card_message=card_message,
            )
        except (KeyError, GitHubMirrorDiscordError) as error:
            await respond_interaction(interaction, _public_error(error))
            return
        await self._interaction_start(
            GitHubMirrorAction.WORK,
            record,
            card_message,
            interaction,
            instruction,
        )

    async def start_from_message(self, message: discord.Message, prompt: str) -> bool:
        try:
            record = self._record_for_message(message)
            if record is None:
                return False
            guild = message.guild
            if guild is None:
                raise GitHubMirrorDiscordError("GitHub tasks require a server message.")
            card = await fetch_card_message(self._client, record, guild)
            response = await self._starter.start(
                GitHubMirrorAction.WORK,
                record,
                card,
                guild,
                message.author,
                message.id,
                prompt,
            )
        except (KeyError, ValueError, RuntimeError) as error:
            await respond_message(message, _public_error(error))
            return True
        except Exception:
            logger.exception("GitHub mirror mention action failed message_id=%s", message.id)
            await respond_message(message, "That GitHub task failed safely. Try again.")
            return True
        await respond_message(message, response)
        return True

    async def reconcile_startup(self) -> None:
        await self._starter.reconcile_startup()

    async def _interaction_start(
        self,
        action: GitHubMirrorAction,
        record: GitHubMirrorRecord,
        card: discord.Message,
        interaction: discord.Interaction,
        instruction: str | None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await respond_interaction(interaction, "GitHub tasks require a server channel.")
            return
        try:
            response = await self._starter.start(
                action,
                record,
                card,
                guild,
                interaction.user,
                interaction.id,
                instruction,
            )
        except (KeyError, ValueError, RuntimeError) as error:
            logger.info(
                "GitHub action rejected mirror_id=%s action=%s error=%s",
                record.mirror_id,
                action.value,
                type(error).__name__,
            )
            response = _public_error(error)
        except Exception:
            logger.exception(
                "GitHub action failed mirror_id=%s action=%s",
                record.mirror_id,
                action.value,
            )
            response = "That GitHub task failed safely. Try again."
        await respond_interaction(interaction, response)

    def _record_for_message(self, message: discord.Message) -> GitHubMirrorRecord | None:
        if message.guild is None:
            return None
        reference_id = getattr(message.reference, "message_id", None)
        channel_id = message.channel.id
        records = self._mirrors.records()
        referenced = [
            record
            for record in records
            if record.guild_id == message.guild.id
            and record.channel_id == channel_id
            and record.card_message_id == reference_id
        ]
        bot_id = getattr(self._client.user, "id", None)
        mentioned = type(bot_id) is int and bot_id in message.raw_mentions
        threaded = [
            record
            for record in records
            if mentioned
            and record.guild_id == message.guild.id
            and record.thread_id == channel_id
        ]
        candidates = referenced or threaded
        if not candidates:
            return None
        if len(candidates) != 1:
            raise GitHubMirrorDiscordError("The GitHub item context is ambiguous.")
        return candidates[0]


def _public_error(error: Exception) -> str:
    if isinstance(error, KeyError):
        return "That GitHub item is no longer available."
    if isinstance(error, (GitHubMirrorDiscordError, GitHubMirrorActionUnavailable)):
        return str(error)
    if isinstance(error, ValueError):
        return "That GitHub task request is invalid. Shorten the instructions and try again."
    if isinstance(error, RuntimeError):
        return str(error)
    return "That GitHub task is unavailable."
