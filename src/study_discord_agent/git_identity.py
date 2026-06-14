import logging
import subprocess

logger = logging.getLogger(__name__)

STUDYOS_GIT_NAME = "StudyOS Org"
STUDYOS_GIT_EMAIL = "agents@studyos.invalid"


def ensure_studyos_git_identity() -> None:
    """Configure the shared runtime Git author for StudyOS agent commits."""
    current_name = _git_config("user.name")
    current_email = _git_config("user.email")
    if current_name == STUDYOS_GIT_NAME and current_email == STUDYOS_GIT_EMAIL:
        return

    _set_git_config("user.name", STUDYOS_GIT_NAME)
    _set_git_config("user.email", STUDYOS_GIT_EMAIL)
    logger.info("configured StudyOS Git author identity")


def _git_config(key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", "--global", "--get", key],
        capture_output=True,
        check=False,
        text=True,
    )
    value = result.stdout.strip()
    return value or None


def _set_git_config(key: str, value: str) -> None:
    subprocess.run(
        ["git", "config", "--global", key, value],
        check=True,
        text=True,
    )


def ensure_git_identity_from_gh() -> None:
    """Backward-compatible wrapper for older startup imports."""
    ensure_studyos_git_identity()
