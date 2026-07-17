from typing import Protocol, cast

import discord

from study_discord_agent.github_mirror_model import GitHubItemKind, GitHubMirrorRecord


class GitHubMirrorDiscordError(RuntimeError):
    pass


class ItemThread(Protocol):
    id: int
    name: str
    parent_id: int | None
    category_id: int | None
    type: discord.ChannelType

    def permissions_for(self, member: object) -> object: ...


class _CardMessage(Protocol):
    id: int
    channel: object

    async def create_thread(self, **kwargs: object) -> object: ...


class _TextChannel(Protocol):
    id: int
    type: discord.ChannelType

    def permissions_for(self, member: object) -> object: ...

    async def fetch_message(self, message_id: int) -> discord.Message: ...


def validated_button(
    record: GitHubMirrorRecord, interaction: discord.Interaction
) -> discord.Message:
    message = interaction.message
    if (
        interaction.guild is None
        or interaction.guild_id != record.guild_id
        or interaction.channel_id != record.channel_id
        or message is None
        or message.id != record.card_message_id
        or getattr(message.channel, "id", None) != record.channel_id
    ):
        raise GitHubMirrorDiscordError("This GitHub card is no longer current.")
    validate_parent_permissions(record, message, interaction.guild, interaction.user)
    return message


def validate_modal(
    record: GitHubMirrorRecord,
    interaction: discord.Interaction,
    *,
    expected_card_id: int,
    expected_actor_id: int,
    card_message: discord.Message,
) -> None:
    if (
        interaction.guild is None
        or interaction.guild_id != record.guild_id
        or interaction.channel_id != record.channel_id
        or interaction.user.id != expected_actor_id
        or record.card_message_id != expected_card_id
        or card_message.id != expected_card_id
    ):
        raise GitHubMirrorDiscordError("This GitHub action is no longer authorized.")
    validate_parent_permissions(record, card_message, interaction.guild, interaction.user)


def validate_parent_permissions(
    record: GitHubMirrorRecord,
    card_message: discord.Message,
    guild: discord.Guild,
    actor: object,
) -> None:
    channel = card_message.channel
    bot = guild.me
    if (
        guild.id != record.guild_id
        or getattr(channel, "id", None) != record.channel_id
        or getattr(channel, "type", None) is not discord.ChannelType.text
        or not callable(getattr(channel, "permissions_for", None))
    ):
        raise GitHubMirrorDiscordError("GitHub actions require the mirrored server text channel.")
    parent = cast(_TextChannel, channel)
    for member, subject in ((actor, "You"), (bot, "StudyOS")):
        permissions = parent.permissions_for(member)
        required = ("view_channel", "create_public_threads", "send_messages_in_threads")
        if not all(getattr(permissions, name, False) is True for name in required):
            raise GitHubMirrorDiscordError(
                f"{subject} cannot create and use a public thread in this channel."
            )


async def fetch_card_message(
    client: discord.Client,
    record: GitHubMirrorRecord,
    guild: discord.Guild,
) -> discord.Message:
    if record.card_message_id is None:
        raise GitHubMirrorDiscordError("This GitHub item has no current Discord card.")
    channel = await _fetch_channel(client, record.channel_id, allow_missing=False)
    if (
        channel is None
        or getattr(channel, "type", None) is not discord.ChannelType.text
        or getattr(channel, "guild", guild) != guild
        or not callable(getattr(channel, "fetch_message", None))
    ):
        raise GitHubMirrorDiscordError("The mirrored GitHub channel is unavailable.")
    try:
        message = await cast(_TextChannel, channel).fetch_message(record.card_message_id)
    except discord.NotFound as error:
        raise GitHubMirrorDiscordError("The mirrored GitHub card is unavailable.") from error
    if message.id != record.card_message_id:
        raise GitHubMirrorDiscordError("Discord returned the wrong GitHub card.")
    return message


async def resolve_or_create_thread(
    client: discord.Client,
    record: GitHubMirrorRecord,
    card_message: discord.Message,
    guild: discord.Guild,
    actor: object,
) -> ItemThread:
    validate_parent_permissions(record, card_message, guild, actor)
    if record.thread_id is not None:
        existing = await _fetch_channel(client, record.thread_id, allow_missing=False)
        return _validated_thread(existing, record, guild, actor)
    recovered = await _fetch_channel(client, record.card_message_id, allow_missing=True)
    if recovered is not None:
        return _validated_thread(recovered, record, guild, actor)
    try:
        created = await cast(_CardMessage, card_message).create_thread(
            name=_thread_name(record),
            auto_archive_duration=1440,
            reason="StudyOS GitHub item task",
        )
    except discord.HTTPException as error:
        raise GitHubMirrorDiscordError(
            "StudyOS could not create the GitHub item thread."
        ) from error
    return _validated_thread(created, record, guild, actor)


async def respond_interaction(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def respond_message(message: discord.Message, content: str) -> None:
    await message.reply(
        content,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _fetch_channel(
    client: discord.Client, channel_id: int | None, *, allow_missing: bool
) -> object | None:
    if channel_id is None:
        return None
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await client.fetch_channel(channel_id)
    except discord.NotFound:
        if allow_missing:
            return None
        raise GitHubMirrorDiscordError("The GitHub item channel is no longer available.") from None


def _validated_thread(
    candidate: object | None,
    record: GitHubMirrorRecord,
    guild: discord.Guild,
    actor: object,
) -> ItemThread:
    bot = guild.me
    if (
        candidate is None
        or getattr(candidate, "type", None) is not discord.ChannelType.public_thread
        or getattr(candidate, "parent_id", None) != record.channel_id
        or getattr(candidate, "locked", False) is True
        or not callable(getattr(candidate, "permissions_for", None))
    ):
        raise GitHubMirrorDiscordError("The GitHub item thread is not a usable public thread.")
    thread = cast(ItemThread, candidate)
    for member, subject in ((actor, "You"), (bot, "StudyOS")):
        permissions = thread.permissions_for(member)
        required = ("view_channel", "send_messages_in_threads")
        if not all(getattr(permissions, name, False) is True for name in required):
            raise GitHubMirrorDiscordError(f"{subject} cannot use the GitHub item thread.")
    return thread


def _thread_name(record: GitHubMirrorRecord) -> str:
    kind = "pr" if record.item_kind is GitHubItemKind.PULL_REQUEST else "issue"
    return f"{kind}-{record.item_number}-studyos"
