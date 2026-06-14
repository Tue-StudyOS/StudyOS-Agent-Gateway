import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from study_discord_agent.git_identity import (
    STUDYOS_GIT_EMAIL,
    STUDYOS_GIT_NAME,
    ensure_git_identity_from_gh,
    ensure_studyos_git_identity,
)


def test_ensure_studyos_git_identity_sets_missing_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = list(args)
        calls.append(command)
        if command[:4] == ["git", "config", "--global", "--get"]:
            return subprocess.CompletedProcess(command, 1, stdout="")
        if command[0] == "gh":
            raise AssertionError(f"unexpected gh command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ensure_studyos_git_identity()

    assert ["git", "config", "--global", "user.name", STUDYOS_GIT_NAME] in calls
    assert ["git", "config", "--global", "user.email", STUDYOS_GIT_EMAIL] in calls


def test_ensure_studyos_git_identity_replaces_personal_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = list(args)
        calls.append(command)
        if command == ["git", "config", "--global", "--get", "user.name"]:
            return subprocess.CompletedProcess(command, 0, stdout="SebastianBoehler\n")
        if command == ["git", "config", "--global", "--get", "user.email"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="27767932+SebastianBoehler@users.noreply.github.com\n",
            )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ensure_studyos_git_identity()

    assert ["git", "config", "--global", "user.name", STUDYOS_GIT_NAME] in calls
    assert ["git", "config", "--global", "user.email", STUDYOS_GIT_EMAIL] in calls


def test_ensure_studyos_git_identity_keeps_matching_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = list(args)
        calls.append(command)
        if command == ["git", "config", "--global", "--get", "user.name"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{STUDYOS_GIT_NAME}\n")
        if command == ["git", "config", "--global", "--get", "user.email"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{STUDYOS_GIT_EMAIL}\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ensure_studyos_git_identity()

    assert calls == [
        ["git", "config", "--global", "--get", "user.name"],
        ["git", "config", "--global", "--get", "user.email"],
    ]


def test_ensure_git_identity_from_gh_keeps_backward_compatible_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        command = list(args)
        calls.append(command)
        if command[:4] == ["git", "config", "--global", "--get"]:
            return subprocess.CompletedProcess(command, 1, stdout="")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ensure_git_identity_from_gh()

    assert ["git", "config", "--global", "user.name", STUDYOS_GIT_NAME] in calls
    assert ["git", "config", "--global", "user.email", STUDYOS_GIT_EMAIL] in calls
