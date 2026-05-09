from study_discord_agent.memory import get_studyos_memory_path


def build_agent_prompt(prompt: str, user: str, channel_id: int, codex_home: str | None) -> str:
    memory_path = get_studyos_memory_path(codex_home)
    return (
        "You are running from the StudyOS Discord/GitHub collaboration gateway.\n"
        f"Before substantial StudyOS work, consult the project memory at {memory_path} "
        "for course context, product direction, collaboration policy, and tone. "
        "If the file is unavailable, continue with the request and mention missing "
        "context only when it affects the answer.\n"
        "Never merge pull requests; humans approve and merge.\n"
        f"Discord user: {user}\n"
        f"Discord channel id: {channel_id}\n\n"
        "User request:\n"
        f"{prompt}\n"
    )
