from dataclasses import dataclass

from study_discord_agent.discord_origin import DiscordOriginContext
from study_discord_agent.discord_task_inputs import StagedDiscordAttachments
from study_discord_agent.discord_task_model import DiscordTaskSourceKind

MAX_TASK_PROMPT_CHARS = 4_000
MAX_TASK_SOURCE_LABEL_CHARS = 200


@dataclass(frozen=True)
class DiscordTaskRequest:
    source_kind: DiscordTaskSourceKind
    guild_id: int
    origin_channel_id: int
    execution_channel_id: int
    owner_id: int
    trigger_event_id: int
    source_message_id: int | None
    prompt: str
    source_label: str
    attachments: StagedDiscordAttachments
    origin_context: DiscordOriginContext | None

    def __post_init__(self) -> None:
        for name in (
            "guild_id",
            "origin_channel_id",
            "execution_channel_id",
            "owner_id",
            "trigger_event_id",
        ):
            _positive_id(getattr(self, name), name)
        _optional_positive_id(self.source_message_id, "source_message_id")
        _bounded_text(self.prompt, "prompt", MAX_TASK_PROMPT_CHARS)
        _bounded_text(
            self.source_label,
            "source_label",
            MAX_TASK_SOURCE_LABEL_CHARS,
        )


@dataclass(frozen=True)
class DiscordTaskSteerRequest:
    prompt: str
    source_message_id: int | None
    attachments: StagedDiscordAttachments
    origin_context: DiscordOriginContext | None

    def __post_init__(self) -> None:
        _bounded_text(self.prompt, "prompt", MAX_TASK_PROMPT_CHARS)
        _optional_positive_id(self.source_message_id, "source_message_id")


def _positive_id(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _optional_positive_id(value: object, name: str) -> None:
    if value is not None:
        _positive_id(value, name)


def _bounded_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum} characters")
