from dataclasses import dataclass

from study_discord_agent.codex_app_server_protocol import ApprovalPolicy, SandboxMode
from study_discord_agent.codex_command import is_codex_exec_command


@dataclass(frozen=True)
class CodexAppServerLaunch:
    command: tuple[str, ...]
    model: str | None
    model_provider: str | None
    approval_policy: ApprovalPolicy | None
    sandbox: SandboxMode | None
    cwd: str | None


def parse_codex_app_server_command(args: list[str]) -> CodexAppServerLaunch:
    if not is_codex_exec_command(args):
        raise ValueError("Codex app-server requires a codex exec command")
    command = [args[0], "app-server", "--listen", "stdio://"]
    values: dict[str, str | None] = {
        "model": None,
        "approval": None,
        "sandbox": None,
        "cwd": None,
        "local_provider": None,
    }
    model_provider: str | None = None
    index = 2
    while index < len(args):
        option = args[index]
        if option in {"-c", "--config", "--enable", "--disable"}:
            value = _required_value(args, index, option)
            command.extend([option, value])
            index += 2
            continue
        if option == "--strict-config":
            command.append(option)
        elif option in {"-m", "--model"}:
            values["model"] = _required_value(args, index, option)
            index += 1
        elif option in {"-a", "--ask-for-approval"}:
            values["approval"] = _required_value(args, index, option)
            index += 1
        elif option in {"-s", "--sandbox"}:
            values["sandbox"] = _required_value(args, index, option)
            index += 1
        elif option in {"-C", "--cd"}:
            values["cwd"] = _required_value(args, index, option)
            index += 1
        elif option == "--local-provider":
            values["local_provider"] = _required_value(args, index, option)
            model_provider = "oss"
            index += 1
        elif option == "--oss":
            model_provider = "oss"
        elif option == "--dangerously-bypass-approvals-and-sandbox":
            values["approval"] = "never"
            values["sandbox"] = "danger-full-access"
        elif option == "--json" or option == "-" and index == len(args) - 1:
            pass
        elif matched := _long_option_value(option):
            name, value = matched
            if name in {"--config", "--enable", "--disable"}:
                command.append(option)
            elif name == "--model":
                values["model"] = value
            elif name == "--ask-for-approval":
                values["approval"] = value
            elif name == "--sandbox":
                values["sandbox"] = value
            elif name == "--cd":
                values["cwd"] = value
            elif name == "--local-provider":
                values["local_provider"] = value
                model_provider = "oss"
            else:
                raise _unsupported(option)
        else:
            raise _unsupported(option)
        index += 1

    local_provider = values["local_provider"]
    if local_provider:
        if local_provider not in {"ollama", "lmstudio"}:
            raise ValueError(f"Unsupported Codex local provider: {local_provider}")
        command.extend(["-c", f'oss_provider="{local_provider}"'])
    return CodexAppServerLaunch(
        command=tuple(command),
        model=values["model"],
        model_provider=model_provider,
        approval_policy=_approval_policy(values["approval"]),
        sandbox=_sandbox(values["sandbox"]),
        cwd=values["cwd"],
    )


def _required_value(args: list[str], index: int, option: str) -> str:
    if index + 1 >= len(args) or not args[index + 1]:
        raise ValueError(f"Codex option {option} requires a value")
    return args[index + 1]


def _long_option_value(option: str) -> tuple[str, str] | None:
    if not option.startswith("--") or "=" not in option:
        return None
    name, value = option.split("=", 1)
    if not value:
        raise ValueError(f"Codex option {name} requires a value")
    return name, value


def _approval_policy(value: str | None) -> ApprovalPolicy | None:
    if value is None:
        return value
    if value == "untrusted":
        return "untrusted"
    if value == "on-request":
        return "on-request"
    if value == "never":
        return "never"
    raise ValueError(f"Unsupported app-server approval policy: {value}")


def _sandbox(value: str | None) -> SandboxMode | None:
    if value is None:
        return value
    if value == "read-only":
        return "read-only"
    if value == "workspace-write":
        return "workspace-write"
    if value == "danger-full-access":
        return "danger-full-access"
    raise ValueError(f"Unsupported app-server sandbox mode: {value}")


def _unsupported(option: str) -> ValueError:
    return ValueError(f"Unsupported AGENT_COMMAND option for Codex app-server: {option}")
