import pytest

from study_discord_agent.codex_app_server_command import parse_codex_app_server_command


def test_live_command_maps_policy_and_cwd() -> None:
    launch = parse_codex_app_server_command(
        [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            "/workspaces",
            "-",
        ]
    )

    assert launch.command == ("codex", "app-server", "--listen", "stdio://")
    assert launch.approval_policy == "never"
    assert launch.sandbox == "danger-full-access"
    assert launch.cwd == "/workspaces"


def test_oss_provider_is_translated_to_app_server_config() -> None:
    launch = parse_codex_app_server_command(
        ["codex", "exec", "--oss", "--local-provider", "ollama", "--json", "-"]
    )

    assert launch.model_provider == "oss"
    assert launch.command[-2:] == ("-c", 'oss_provider="ollama"')


def test_unsupported_exec_option_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="--profile"):
        parse_codex_app_server_command(["codex", "exec", "--profile", "local", "-"])
