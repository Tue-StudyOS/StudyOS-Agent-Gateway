import hashlib
import hmac

from study_discord_agent.security import verify_github_signature


def test_verify_github_signature_accepts_valid_signature() -> None:
    body = b'{"action":"opened"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_github_signature("secret", body, f"sha256={digest}")


def test_verify_github_signature_rejects_invalid_signature() -> None:
    assert not verify_github_signature("secret", b"{}", "sha256=bad")
