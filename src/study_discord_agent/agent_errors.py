class AgentTurnTimedOut(RuntimeError):
    """The agent did not finish before its configured deadline."""


class AgentRuntimeDisconnected(RuntimeError):
    """The persistent agent runtime disconnected during a task."""


class AgentRuntimeIncompatible(RuntimeError):
    """The configured persistent agent runtime cannot serve this gateway."""


class AgentProcessFailed(RuntimeError):
    """A one-shot agent process exited without a usable reply."""


class AgentInvalidOutput(RuntimeError):
    """An agent response could not be used safely."""


class AgentConfigurationError(RuntimeError):
    """The gateway does not have the required agent configuration."""


class AgentWorkspaceOrAttachmentError(RuntimeError):
    """A workspace or reply attachment could not be prepared safely."""
