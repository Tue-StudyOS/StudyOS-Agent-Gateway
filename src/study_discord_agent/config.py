from functools import cached_property

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: SecretStr
    discord_guild_id: int | None = None
    discord_pr_channel_id: int | None = None

    github_webhook_secret: SecretStr | None = None
    github_token: SecretStr | None = None
    github_repository: str | None = None

    discord_message_agent_enabled: bool = True
    discord_attachment_dir: str = "/tmp/studyos-discord-attachments"
    discord_artifact_allowed_roots: str = "/tmp/studyos-artifacts,/workspaces,/workspace"
    discord_artifact_max_bytes: int = 8_000_000
    discord_proactive_agent_enabled: bool = False
    discord_proactive_interval_seconds: int = 900
    discord_proactive_recent_activity_seconds: int = 1800
    discord_proactive_min_post_interval_seconds: int = 3600
    discord_proactive_dry_run: bool = True

    agent_webhook_url: str | None = None
    agent_command: str | None = None
    agent_workdir: str | None = None
    agent_timeout_seconds: int = 900
    agent_auto_review_enabled: bool = False
    agent_channel_sessions_enabled: bool = True
    agent_session_store_path: str | None = None

    github_poll_enabled: bool = False
    github_poll_interval_seconds: int = 1800
    github_poll_limit: int = 20

    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    codex_home: str | None = None

    @field_validator(
        "discord_guild_id",
        "discord_pr_channel_id",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("github_repository")
    @classmethod
    def validate_repository(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value.count("/") != 1:
            raise ValueError("GITHUB_REPOSITORY must use owner/name format")
        return value

    @cached_property
    def github_token_value(self) -> str | None:
        return self.github_token.get_secret_value() if self.github_token else None

    @cached_property
    def webhook_secret_value(self) -> str | None:
        if not self.github_webhook_secret:
            return None
        return self.github_webhook_secret.get_secret_value() or None

    @cached_property
    def discord_token_value(self) -> str:
        return self.discord_token.get_secret_value()

    @cached_property
    def discord_artifact_allowed_root_list(self) -> tuple[str, ...]:
        return tuple(
            item.strip()
            for item in self.discord_artifact_allowed_roots.split(",")
            if item.strip()
        )


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
