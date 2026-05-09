from functools import cached_property

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: SecretStr
    discord_guild_id: int | None = None
    discord_pr_channel_id: int

    github_webhook_secret: SecretStr
    github_token: SecretStr | None = None
    github_repository: str | None = None
    github_write_enabled: bool = False

    allowed_discord_role_ids: str = ""
    discord_message_agent_enabled: bool = False

    agent_webhook_url: str | None = None
    agent_command: str | None = None
    agent_workdir: str | None = None
    agent_timeout_seconds: int = 900
    agent_auto_review_enabled: bool = False

    github_poll_enabled: bool = False
    github_poll_interval_seconds: int = 1800
    github_poll_limit: int = 20

    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"

    @field_validator("github_repository")
    @classmethod
    def validate_repository(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value.count("/") != 1:
            raise ValueError("GITHUB_REPOSITORY must use owner/name format")
        return value

    @cached_property
    def allowed_role_ids(self) -> frozenset[int]:
        if not self.allowed_discord_role_ids.strip():
            return frozenset()
        values: list[int] = []
        for item in self.allowed_discord_role_ids.split(","):
            item = item.strip()
            if item:
                values.append(int(item))
        return frozenset(values)

    @cached_property
    def github_token_value(self) -> str | None:
        return self.github_token.get_secret_value() if self.github_token else None

    @cached_property
    def webhook_secret_value(self) -> str:
        return self.github_webhook_secret.get_secret_value()

    @cached_property
    def discord_token_value(self) -> str:
        return self.discord_token.get_secret_value()


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
