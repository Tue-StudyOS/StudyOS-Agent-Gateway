import logging

import discord

from study_discord_agent.discord_task_persistence import TaskStoreDurabilityError
from study_discord_agent.github_mirror_cards import (
    github_mirror_card_signature,
    github_mirror_view,
)
from study_discord_agent.github_mirror_channel import (
    GitHubMirrorChannelAccessError,
    GitHubMirrorConfigurationError,
    MirrorChannel,
    MirrorChannelClient,
    MirrorMessage,
    find_bot_delivery_messages,
    resolve_mirror_channel,
)
from study_discord_agent.github_mirror_model import GitHubMirrorEvent, GitHubMirrorRecord
from study_discord_agent.github_mirror_store import GitHubMirrorStore

logger = logging.getLogger(__name__)
_RECONCILE_ATTEMPTS = 8

__all__ = (
    "GitHubMirrorChannelAccessError",
    "GitHubMirrorConfigurationError",
    "GitHubMirrorPublisher",
)


class GitHubMirrorPublisher:
    def __init__(
        self,
        client: MirrorChannelClient,
        store: GitHubMirrorStore,
        *,
        guild_id: int | None,
        channel_id: int | None,
    ) -> None:
        self._client = client
        self._store = store
        self._guild_id = guild_id
        self._channel_id = channel_id

    async def publish(self, event: GitHubMirrorEvent) -> GitHubMirrorRecord:
        if self._guild_id is None or self._channel_id is None:
            raise GitHubMirrorConfigurationError(
                "DISCORD_GUILD_ID and DISCORD_PR_CHANNEL_ID are required for GitHub mirrors"
            )
        upsert = self._store.upsert_event(
            event,
            guild_id=self._guild_id,
            channel_id=self._channel_id,
        )
        return await self.publish_staged(upsert.record.mirror_id)

    async def publish_staged(self, mirror_id: str) -> GitHubMirrorRecord:
        record = self._store.get(mirror_id)
        if (record.guild_id, record.channel_id) != (self._guild_id, self._channel_id):
            raise GitHubMirrorConfigurationError(
                "Persisted GitHub mirror destination does not match this publisher"
            )
        channel = await resolve_mirror_channel(
            self._client,
            guild_id=self._guild_id,
            channel_id=self._channel_id,
        )
        try:
            for _ in range(_RECONCILE_ATTEMPTS):
                record = self._store.get(mirror_id)
                if record.card_message_id is None:
                    delivered = await self._create_card(channel, record)
                else:
                    delivered = await self._finish_attached_card(channel, record)
                completed = self._store.complete_publication(
                    mirror_id,
                    delivered.revision,
                )
                if not completed.publication_pending:
                    return completed
            raise RuntimeError("GitHub mirror publication did not reach a stable revision")
        except Exception:
            logger.exception("GitHub mirror publication failed mirror_id=%s", record.mirror_id)
            raise

    async def _create_card(
        self, channel: MirrorChannel, record: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        record, claimed = self._store.claim_card_creation(record.mirror_id)
        if record.card_message_id is not None:
            return await self._finish_attached_card(channel, record)
        nonce = record.card_create_nonce
        if nonce is None:
            raise RuntimeError("pending Discord mirror card creation has no nonce")
        if not claimed:
            matches = await find_bot_delivery_messages(channel, nonce)
            if matches:
                return await self._attach_created_cards(channel, matches, record)
        try:
            message = await channel.send(
                content=None,
                view=github_mirror_view(record),
                nonce=nonce,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as error:
            self._store.release_card_creation(record.mirror_id, nonce)
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
        except discord.HTTPException:
            matches = await find_bot_delivery_messages(channel, nonce)
            if matches:
                return await self._attach_created_cards(channel, matches, record)
            raise
        return await self._attach_created_cards(channel, (message,), record)

    async def _attach_created_cards(
        self,
        channel: MirrorChannel,
        messages: tuple[MirrorMessage, ...],
        record: GitHubMirrorRecord,
    ) -> GitHubMirrorRecord:
        message = min(messages, key=lambda candidate: candidate.id)
        if type(message.id) is not int or message.id <= 0:
            raise RuntimeError("Discord returned an invalid mirror card ID")
        nonce = record.card_create_nonce
        if nonce is None:
            raise RuntimeError("pending Discord mirror card creation has no nonce")
        try:
            retained, attached = self._store.attach_card_if_missing(
                record.mirror_id,
                message.id,
                nonce,
            )
        except TaskStoreDurabilityError:
            await self._best_effort_reconcile_uncertain_attachment(channel, record, message)
            raise
        except Exception:
            if await self._delete_uncommitted_cards(messages, record.mirror_id):
                self._store.release_card_creation(record.mirror_id, nonce)
            raise
        retained = await self._finish_attached_card(channel, retained, message=message)
        action = "published" if attached else "reconciled raced"
        logger.info("%s GitHub mirror card mirror_id=%s", action, record.mirror_id)
        return retained

    async def _delete_uncommitted_cards(
        self, messages: tuple[MirrorMessage, ...], mirror_id: str
    ) -> bool:
        removed = True
        for message in messages:
            try:
                await message.delete()
            except discord.NotFound:
                continue
            except Exception:
                removed = False
                logger.exception(
                    "failed to delete uncommitted GitHub mirror card mirror_id=%s",
                    mirror_id,
                )
        return removed

    async def _best_effort_reconcile_uncertain_attachment(
        self,
        channel: MirrorChannel,
        rendered: GitHubMirrorRecord,
        message: MirrorMessage,
    ) -> None:
        try:
            canonical = self._store.get(rendered.mirror_id)
            if canonical.card_message_id is not None:
                await self._finish_attached_card(channel, canonical, message=message)
        except Exception:
            logger.exception(
                "failed to reconcile durability-uncertain GitHub mirror card mirror_id=%s",
                rendered.mirror_id,
            )

    async def _finish_attached_card(
        self,
        channel: MirrorChannel,
        record: GitHubMirrorRecord,
        *,
        message: MirrorMessage | None = None,
        redirects: int = 0,
    ) -> GitHubMirrorRecord:
        if redirects >= _RECONCILE_ATTEMPTS:
            raise RuntimeError("GitHub mirror card identity did not reach a stable revision")
        assert record.card_message_id is not None
        if message is None or message.id != record.card_message_id:
            try:
                message = await channel.fetch_message(record.card_message_id)
            except discord.NotFound:
                return await self._replace_missing_card(channel, record)
            except discord.Forbidden as error:
                raise GitHubMirrorChannelAccessError(
                    "Configured Discord PR channel is inaccessible"
                ) from error
        try:
            await self._render_card(message, record)
            retained = await self._reconcile_card(message, record)
            if retained.card_message_id != message.id:
                return await self._finish_attached_card(
                    channel,
                    retained,
                    redirects=redirects + 1,
                )
            retained = await self._cleanup_duplicates(channel, message, retained)
            if retained.card_message_id != message.id:
                return await self._finish_attached_card(
                    channel,
                    retained,
                    redirects=redirects + 1,
                )
            return retained
        except discord.NotFound:
            return await self._replace_missing_card(channel, record)

    async def _cleanup_duplicates(
        self,
        channel: MirrorChannel,
        message: MirrorMessage,
        record: GitHubMirrorRecord,
    ) -> GitHubMirrorRecord:
        nonce = record.card_cleanup_nonce
        if nonce is None:
            return record
        await self._delete_nonce_matches(channel, nonce, keep_message_id=message.id)
        cleaned = self._store.complete_card_cleanup(record.mirror_id, nonce)
        if github_mirror_card_signature(cleaned) == github_mirror_card_signature(record):
            return cleaned
        await self._render_card(message, cleaned)
        return await self._reconcile_card(message, cleaned)

    async def _delete_nonce_matches(
        self,
        channel: MirrorChannel,
        nonce: str,
        *,
        keep_message_id: int | None,
    ) -> None:
        matches = await find_bot_delivery_messages(channel, nonce)
        for candidate in matches:
            if candidate.id == keep_message_id:
                continue
            try:
                await candidate.delete()
            except discord.NotFound:
                continue

    async def _replace_missing_card(
        self, channel: MirrorChannel, record: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        assert record.card_message_id is not None
        missing_message_id = record.card_message_id
        if record.card_cleanup_nonce is not None:
            await self._delete_nonce_matches(
                channel,
                record.card_cleanup_nonce,
                keep_message_id=None,
            )
            record = self._store.complete_card_cleanup(
                record.mirror_id,
                record.card_cleanup_nonce,
            )
        cleared = self._store.clear_card_if_matches(record.mirror_id, missing_message_id)
        if cleared.card_message_id is None:
            return await self._create_card(channel, cleared)
        return await self._finish_attached_card(channel, cleared)

    async def _reconcile_card(
        self, message: MirrorMessage, rendered: GitHubMirrorRecord
    ) -> GitHubMirrorRecord:
        rendered_revision = rendered.revision
        for _ in range(_RECONCILE_ATTEMPTS):
            canonical = self._store.get(rendered.mirror_id)
            if canonical.card_message_id != message.id:
                return canonical
            if canonical.revision != rendered_revision:
                await self._render_card(message, canonical)
                rendered_revision = canonical.revision
            latest = self._store.get(rendered.mirror_id)
            if latest.card_message_id != message.id:
                return latest
            if latest.revision == rendered_revision:
                return latest
        raise RuntimeError("GitHub mirror card did not reach a stable revision")

    async def _render_card(self, message: MirrorMessage, record: GitHubMirrorRecord) -> None:
        try:
            await message.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=github_mirror_view(record),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden as error:
            raise GitHubMirrorChannelAccessError(
                "Configured Discord PR channel is inaccessible"
            ) from error
