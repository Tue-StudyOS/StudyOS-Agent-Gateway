from dataclasses import dataclass


class DiscordTaskChannelBusy(RuntimeError):
    pass


class DiscordTaskActionUnavailable(RuntimeError):
    pass


class DiscordTaskServiceClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscordTaskControlState:
    steering: bool
    resumable: bool
    continuable: bool
