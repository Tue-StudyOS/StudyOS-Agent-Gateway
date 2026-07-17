import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from study_discord_agent.codex_app_server_protocol import (
    ApprovalPolicy,
    JsonObject,
    SandboxMode,
)


class AgentPolicyClass(StrEnum):
    REVIEW = "review"
    SECURITY_REVIEW = "security_review"
    VULNERABILITY_SCAN = "vulnerability_scan"
    IMPLEMENTATION = "implementation"


@dataclass(frozen=True)
class AgentExecutionPolicy:
    policy_class: AgentPolicyClass
    approval_policy: ApprovalPolicy
    sandbox_mode: SandboxMode
    network_access: bool
    environment_access: bool
    dynamic_tools: bool
    version: int = 1

    @property
    def fingerprint(self) -> str:
        payload = {
            "approval_policy": self.approval_policy,
            "dynamic_tools": self.dynamic_tools,
            "environment_access": self.environment_access,
            "network_access": self.network_access,
            "policy_class": self.policy_class.value,
            "sandbox_mode": self.sandbox_mode,
            "version": self.version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def sandbox_policy(self) -> JsonObject:
        if self.sandbox_mode == "read-only":
            return {"type": "readOnly", "networkAccess": self.network_access}
        if self.sandbox_mode == "workspace-write":
            return {
                "type": "workspaceWrite",
                "networkAccess": self.network_access,
                "writableRoots": [],
            }
        raise ValueError("Restricted execution cannot use full-access sandboxing")


def execution_policy(policy_class: AgentPolicyClass) -> AgentExecutionPolicy:
    sandbox: Literal["read-only", "workspace-write"] = (
        "workspace-write"
        if policy_class is AgentPolicyClass.IMPLEMENTATION
        else "read-only"
    )
    return AgentExecutionPolicy(
        policy_class=policy_class,
        approval_policy="never",
        sandbox_mode=sandbox,
        network_access=False,
        environment_access=False,
        dynamic_tools=False,
    )
