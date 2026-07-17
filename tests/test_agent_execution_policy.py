from study_discord_agent.agent_execution_policy import (
    AgentPolicyClass,
    execution_policy,
)


def test_fixed_github_policies_are_stable_and_minimally_privileged() -> None:
    review = execution_policy(AgentPolicyClass.REVIEW)
    security = execution_policy(AgentPolicyClass.SECURITY_REVIEW)
    scan = execution_policy(AgentPolicyClass.VULNERABILITY_SCAN)
    implementation = execution_policy(AgentPolicyClass.IMPLEMENTATION)

    for policy in (review, security, scan, implementation):
        assert policy.approval_policy == "never"
        assert not policy.network_access
        assert not policy.environment_access
        assert not policy.dynamic_tools
        assert len(policy.fingerprint) == 64

    assert review.sandbox_mode == "read-only"
    assert security.sandbox_mode == "read-only"
    assert scan.sandbox_mode == "read-only"
    assert implementation.sandbox_mode == "workspace-write"
    assert review.fingerprint != security.fingerprint
    assert scan.fingerprint == execution_policy(
        AgentPolicyClass.VULNERABILITY_SCAN
    ).fingerprint
    assert scan.sandbox_policy == {"type": "readOnly", "networkAccess": False}
    assert execution_policy(AgentPolicyClass.IMPLEMENTATION).sandbox_policy == {
        "type": "workspaceWrite",
        "networkAccess": False,
        "writableRoots": [],
    }
