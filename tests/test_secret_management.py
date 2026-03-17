"""Tests for enhanced _get_secret() in jwt_auth.py.

Covers:
- File-based secret: POND_JWT_SECRET_FILE → reads secret from file
- Missing file → HTTPException(500)
- Weak secret rejected on startup (validate_secret_strength)
- Versioned secret try/fallback for token rotation
  - Primary secret POND_JWT_SECRET_V2, fallback POND_JWT_SECRET_V1
  - Token signed with old secret still verifies via fallback
  - Token signed with new secret verifies against primary
  - Only old-version secret configured: fallback acts as primary
"""

import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STRONG_SECRET = "this-is-a-very-strong-secret-key-32plus-chars"
WEAK_SECRETS = [
    "short",  # too short
    "password",  # common word
    "12345678",  # only digits
    "secret",  # common word
    "abc",  # too short
    "",  # empty
]


# ---------------------------------------------------------------------------
# File-based secret: happy path
# ---------------------------------------------------------------------------


def test_file_secret_returns_contents_when_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POND_JWT_SECRET_FILE pointing to a valid file → _get_secret() returns file contents."""
    secret_file = tmp_path / "jwt_secret.txt"
    secret_file.write_text(STRONG_SECRET)

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == STRONG_SECRET


def test_file_secret_strips_trailing_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Secret files often have a trailing newline — it must be stripped."""
    secret_file = tmp_path / "jwt_secret.txt"
    secret_file.write_text(STRONG_SECRET + "\n")

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == STRONG_SECRET


def test_file_secret_preferred_over_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both POND_JWT_SECRET_FILE and POND_JWT_SECRET are set, file takes precedence."""
    secret_file = tmp_path / "jwt_secret.txt"
    file_secret = "file-based-secret-that-is-strong-enough-for-testing"
    secret_file.write_text(file_secret)

    monkeypatch.setenv("POND_JWT_SECRET", "env-secret-ignored")
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == file_secret


def test_file_secret_can_create_and_verify_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens created using a file-based secret must be verifiable with the same secret."""
    secret_file = tmp_path / "jwt_secret.txt"
    secret_file.write_text(STRONG_SECRET)

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    token = jwt_module.create_access_token("test-tenant")
    claims = jose_jwt.decode(token, STRONG_SECRET, algorithms=["HS256"])
    assert claims["tenant_id"] == "test-tenant"


# ---------------------------------------------------------------------------
# File-based secret: error cases
# ---------------------------------------------------------------------------


def test_missing_secret_file_raises_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POND_JWT_SECRET_FILE pointing to a non-existent file → HTTPException(500)."""
    non_existent = str(tmp_path / "does_not_exist.txt")

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", non_existent)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module._get_secret()

    assert exc_info.value.status_code == 500


def test_missing_secret_file_error_mentions_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 500 detail message should indicate the missing file path."""
    non_existent = str(tmp_path / "missing_secret.txt")

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", non_existent)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module._get_secret()

    # detail should mention the missing file or the env var
    detail = str(exc_info.value.detail).lower()
    assert "file" in detail or "secret" in detail


def test_empty_secret_file_raises_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that exists but is empty → HTTPException(500)."""
    secret_file = tmp_path / "empty_secret.txt"
    secret_file.write_text("")

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module._get_secret()

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Weak secret validation
# ---------------------------------------------------------------------------


def test_validate_secret_strength_function_exists() -> None:
    """validate_secret_strength must be importable from jwt_auth."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    assert hasattr(jwt_module, "validate_secret_strength"), (
        "jwt_auth must expose validate_secret_strength(secret: str) -> None"
    )


@pytest.mark.parametrize("weak_secret", WEAK_SECRETS)
def test_weak_secret_raises_value_error(weak_secret: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_secret_strength must raise ValueError for weak/short secrets."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises((ValueError, HTTPException)):
        jwt_module.validate_secret_strength(weak_secret)


def test_strong_secret_passes_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long, random-looking secret must pass validation without raising."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Should not raise
    jwt_module.validate_secret_strength(STRONG_SECRET)


def test_secret_minimum_length_is_at_least_16_chars() -> None:
    """Secrets shorter than 16 chars must always be rejected."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises((ValueError, HTTPException)):
        jwt_module.validate_secret_strength("a" * 15)


def test_secret_16_or_more_chars_may_pass() -> None:
    """A secret of exactly 32 mixed chars should pass (not too short)."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    secret = "aB3!aB3!aB3!aB3!aB3!aB3!aB3!aB3!"  # 32 chars with mixed content
    # Should not raise
    jwt_module.validate_secret_strength(secret)


def test_startup_validates_pond_jwt_secret_weakness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting POND_JWT_SECRET to a weak value should cause startup validation to fail."""
    monkeypatch.setenv("POND_JWT_SECRET", "weak")
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Startup validation: calling the validation function with the env var should raise
    with pytest.raises((ValueError, HTTPException)):
        jwt_module.validate_secret_strength(os.environ.get("POND_JWT_SECRET", ""))


def test_startup_validation_can_be_triggered_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_startup_secret() must raise if POND_JWT_SECRET is weak or missing."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    assert hasattr(jwt_module, "validate_startup_secret"), (
        "jwt_auth must expose validate_startup_secret() that checks configured secret on startup"
    )


def test_validate_startup_secret_passes_with_strong_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_startup_secret() must not raise when a strong secret is configured."""
    monkeypatch.setenv("POND_JWT_SECRET", STRONG_SECRET)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Should not raise
    jwt_module.validate_startup_secret()


def test_validate_startup_secret_raises_with_weak_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_startup_secret() must raise for a weak POND_JWT_SECRET."""
    monkeypatch.setenv("POND_JWT_SECRET", "tooShort")
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises((ValueError, HTTPException, RuntimeError)):
        jwt_module.validate_startup_secret()


def test_validate_startup_secret_raises_when_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_startup_secret() must raise when no secret is configured at all."""
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises((ValueError, HTTPException, RuntimeError)):
        jwt_module.validate_startup_secret()


def test_validate_startup_secret_passes_with_file_based_strong_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_startup_secret() passes when a strong secret is in POND_JWT_SECRET_FILE."""
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(STRONG_SECRET)

    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(secret_file))

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Should not raise
    jwt_module.validate_startup_secret()


# ---------------------------------------------------------------------------
# Versioned secret try/fallback
# ---------------------------------------------------------------------------


def test_versioned_secret_env_vars_exist_in_docs_or_code() -> None:
    """_get_secret() should support POND_JWT_SECRET_V2 (primary) and POND_JWT_SECRET_V1 (fallback)."""
    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # The function must exist and be callable
    assert callable(jwt_module._get_secret)


def test_primary_versioned_secret_used_when_v2_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When POND_JWT_SECRET_V2 is set, _get_secret() returns it as the primary secret."""
    v2 = "v2-secret-that-is-strong-enough-for-jwt-auth"
    v1 = "v1-old-secret-that-is-strong-enough-for-fallback"

    monkeypatch.setenv("POND_JWT_SECRET_V2", v2)
    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == v2


def test_fallback_to_v1_when_only_v1_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only POND_JWT_SECRET_V1 is set (no V2), _get_secret() returns V1."""
    v1 = "v1-only-secret-that-is-strong-enough-for-fallback"

    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET_V2", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == v1


def test_verify_token_falls_back_to_v1_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token signed with V1 secret must still verify when V1 is configured as fallback."""
    v1 = "v1-old-secret-that-is-strong-enough-for-fallback"
    v2 = "v2-new-secret-that-is-strong-enough-for-rotation"

    # Sign with v1 (old secret)
    old_token = jose_jwt.encode(
        {
            "sub": "tenant-a",
            "tenant_id": "tenant-a",
            "scopes": ["query", "read", "write"],
            "type": "access",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        v1,
        algorithm="HS256",
    )

    # Configure with V2 primary + V1 fallback
    monkeypatch.setenv("POND_JWT_SECRET_V2", v2)
    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Verifying a V1-signed token should succeed via fallback
    claims = jwt_module.verify_access_token(old_token)
    assert claims["tenant_id"] == "tenant-a"


def test_verify_token_uses_v2_as_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token signed with V2 must verify correctly when V2 is the primary secret."""
    v1 = "v1-old-secret-that-is-strong-enough-for-fallback"
    v2 = "v2-new-secret-that-is-strong-enough-for-rotation"

    monkeypatch.setenv("POND_JWT_SECRET_V2", v2)
    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # Create a token — should use V2
    token = jwt_module.create_access_token("tenant-b")

    # Must be verifiable with V2
    claims = jose_jwt.decode(token, v2, algorithms=["HS256"])
    assert claims["tenant_id"] == "tenant-b"


def test_token_signed_with_unknown_secret_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token signed with an unknown third secret must NOT verify even with fallback configured."""
    v1 = "v1-old-secret-that-is-strong-enough-for-fallback"
    v2 = "v2-new-secret-that-is-strong-enough-for-rotation"
    unknown = "completely-different-secret-not-configured"

    unknown_token = jose_jwt.encode(
        {
            "sub": "attacker",
            "tenant_id": "attacker",
            "scopes": ["query"],
            "type": "access",
            "exp": int(time.time()) + 3600,
        },
        unknown,
        algorithm="HS256",
    )

    monkeypatch.setenv("POND_JWT_SECRET_V2", v2)
    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module.verify_access_token(unknown_token)

    assert exc_info.value.status_code == 401


def test_get_all_secrets_returns_list_of_configured_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_all_secrets() must return [v2, v1] when both are configured (for fallback verification)."""
    v1 = "v1-old-secret-that-is-strong-enough-for-fallback"
    v2 = "v2-new-secret-that-is-strong-enough-for-rotation"

    monkeypatch.setenv("POND_JWT_SECRET_V2", v2)
    monkeypatch.setenv("POND_JWT_SECRET_V1", v1)
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    assert hasattr(jwt_module, "_get_all_secrets"), (
        "jwt_auth must expose _get_all_secrets() -> list[str] for versioned fallback"
    )
    secrets = jwt_module._get_all_secrets()
    assert isinstance(secrets, list)
    assert v2 in secrets
    assert v1 in secrets
    # Primary (V2) should come first
    assert secrets[0] == v2


def test_get_all_secrets_with_only_base_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only POND_JWT_SECRET is set, _get_all_secrets() returns a single-element list."""
    monkeypatch.setenv("POND_JWT_SECRET", STRONG_SECRET)
    monkeypatch.delenv("POND_JWT_SECRET_V1", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_V2", raising=False)
    monkeypatch.delenv("POND_JWT_SECRET_FILE", raising=False)

    import importlib
    import ponddb.auth.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    secrets = jwt_module._get_all_secrets()
    assert len(secrets) >= 1
    assert STRONG_SECRET in secrets
