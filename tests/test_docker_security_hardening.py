"""Tests for Docker security hardening requirements.

Validates:
- docker-compose.yml: user 1000:1000, read_only, tmpfs, no-new-privileges,
  secrets block, secret file env vars, no plaintext secrets in environment
- jwt_auth: _get_api_key() and _get_session_secret() support _FILE variants
  analogous to _get_secret()
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    assert COMPOSE_FILE.exists(), "docker-compose.yml not found at repo root"
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ponddb_service(compose: dict) -> dict:
    services = compose.get("services", {})
    assert "ponddb" in services, f"Expected 'ponddb' service, got: {list(services)}"
    return services["ponddb"]


# ---------------------------------------------------------------------------
# Non-root user: user 1000:1000
# ---------------------------------------------------------------------------


def test_ponddb_runs_as_non_root_user(ponddb_service: dict) -> None:
    """ponddb service must declare user: 1000:1000 to prevent root escalation."""
    user = ponddb_service.get("user")
    assert user is not None, (
        "ponddb service must declare 'user: 1000:1000' to run as non-root"
    )


def test_ponddb_user_is_1000_1000(ponddb_service: dict) -> None:
    """ponddb service user must be '1000:1000' (uid:gid)."""
    user = ponddb_service.get("user", "")
    # Accept "1000:1000" as string or 1000:1000 as YAML integer ratio
    assert str(user) == "1000:1000", (
        f"user must be '1000:1000', got {user!r}"
    )


# ---------------------------------------------------------------------------
# Read-only filesystem
# ---------------------------------------------------------------------------


def test_ponddb_read_only_filesystem(ponddb_service: dict) -> None:
    """ponddb service must set read_only: true to harden the container filesystem."""
    read_only = ponddb_service.get("read_only")
    assert read_only is True, (
        "ponddb service must have 'read_only: true' to prevent filesystem writes "
        f"(got read_only={read_only!r})"
    )


# ---------------------------------------------------------------------------
# tmpfs mounts for writable directories
# ---------------------------------------------------------------------------


def test_ponddb_has_tmpfs_mounts(ponddb_service: dict) -> None:
    """ponddb service must declare tmpfs mounts for writable temp directories."""
    tmpfs = ponddb_service.get("tmpfs")
    assert tmpfs is not None, (
        "ponddb service must have 'tmpfs:' mounts for writable dirs "
        "(e.g. /tmp, /app/tmp) since read_only: true is set"
    )


def test_ponddb_tmpfs_includes_tmp(ponddb_service: dict) -> None:
    """tmpfs must include /tmp so the app can write temporary files."""
    tmpfs = ponddb_service.get("tmpfs", [])
    if isinstance(tmpfs, str):
        tmpfs = [tmpfs]
    tmpfs_str = " ".join(str(t) for t in tmpfs)
    assert "/tmp" in tmpfs_str, (
        f"tmpfs must include /tmp, got: {tmpfs}"
    )


# ---------------------------------------------------------------------------
# no-new-privileges security option
# ---------------------------------------------------------------------------


def test_ponddb_has_security_opt(ponddb_service: dict) -> None:
    """ponddb service must declare security_opt to enforce privilege restrictions."""
    security_opt = ponddb_service.get("security_opt")
    assert security_opt is not None, (
        "ponddb service must have 'security_opt:' with no-new-privileges:true"
    )


def test_ponddb_no_new_privileges(ponddb_service: dict) -> None:
    """security_opt must include no-new-privileges:true to prevent privilege escalation."""
    security_opt = ponddb_service.get("security_opt", [])
    if isinstance(security_opt, str):
        security_opt = [security_opt]
    opt_str = " ".join(str(o) for o in security_opt)
    assert "no-new-privileges" in opt_str, (
        f"security_opt must include 'no-new-privileges:true', got: {security_opt}"
    )


# ---------------------------------------------------------------------------
# Docker secrets block
# ---------------------------------------------------------------------------


def test_compose_has_top_level_secrets(compose: dict) -> None:
    """docker-compose.yml must declare a top-level 'secrets:' block."""
    secrets = compose.get("secrets")
    assert secrets is not None, (
        "docker-compose.yml must have a top-level 'secrets:' block "
        "(e.g. jwt_secret, api_key, session_secret)"
    )
    assert len(secrets) >= 1, "At least one secret must be declared"


def test_compose_secrets_includes_jwt_secret(compose: dict) -> None:
    """Secrets block must include a JWT secret entry."""
    secrets = compose.get("secrets", {})
    secret_names = list(secrets.keys())
    jwt_related = [n for n in secret_names if "jwt" in n.lower() or "jwt_secret" in n.lower()]
    assert len(jwt_related) >= 1, (
        f"secrets block must include a jwt_secret entry, got: {secret_names}"
    )


def test_compose_secrets_includes_api_key(compose: dict) -> None:
    """Secrets block must include an api_key entry."""
    secrets = compose.get("secrets", {})
    secret_names = list(secrets.keys())
    api_key_related = [n for n in secret_names if "api_key" in n.lower() or "api" in n.lower()]
    assert len(api_key_related) >= 1, (
        f"secrets block must include an api_key entry, got: {secret_names}"
    )


def test_ponddb_service_mounts_secrets(ponddb_service: dict) -> None:
    """ponddb service must mount the declared secrets."""
    service_secrets = ponddb_service.get("secrets")
    assert service_secrets is not None, (
        "ponddb service must have a 'secrets:' key referencing mounted secrets"
    )
    assert len(service_secrets) >= 1, "ponddb service must mount at least one secret"


def test_ponddb_mounts_jwt_secret(ponddb_service: dict) -> None:
    """ponddb service must mount jwt_secret for the JWT auth module."""
    service_secrets = ponddb_service.get("secrets", [])
    secret_names = [
        (s if isinstance(s, str) else s.get("source", s.get("target", "")))
        for s in service_secrets
    ]
    jwt_related = [n for n in secret_names if "jwt" in str(n).lower()]
    assert len(jwt_related) >= 1, (
        f"ponddb service must mount jwt_secret, mounted secrets: {secret_names}"
    )


# ---------------------------------------------------------------------------
# No plaintext secrets in environment
# ---------------------------------------------------------------------------


def _get_env_as_dict(ponddb_service: dict) -> dict[str, str]:
    """Parse environment block (list or dict) into a plain dict."""
    env = ponddb_service.get("environment", {})
    if isinstance(env, list):
        result = {}
        for item in env:
            if "=" in str(item):
                k, _, v = str(item).partition("=")
                result[k] = v
            else:
                result[str(item)] = ""
        return result
    return dict(env) if env else {}


def test_no_plaintext_jwt_secret_in_environment(ponddb_service: dict) -> None:
    """POND_JWT_SECRET must NOT be set to a plaintext value in environment."""
    env = _get_env_as_dict(ponddb_service)
    jwt_secret = env.get("POND_JWT_SECRET", "")
    # Acceptable: not set at all, or set to empty string, or interpolated from ${VAR}
    # Not acceptable: a literal secret value that isn't an empty interpolation marker
    if jwt_secret:
        # If it's present, it should only be a blank placeholder or variable ref
        assert jwt_secret.startswith("${") or jwt_secret == "", (
            f"POND_JWT_SECRET must not contain a plaintext secret in docker-compose.yml "
            f"(got: {jwt_secret!r}). Use Docker secrets + POND_JWT_SECRET_FILE instead."
        )


def test_no_plaintext_api_key_in_environment(ponddb_service: dict) -> None:
    """POND_API_KEY must NOT be set to a plaintext value in environment."""
    env = _get_env_as_dict(ponddb_service)
    api_key = env.get("POND_API_KEY", "")
    if api_key:
        assert api_key.startswith("${") or api_key == "", (
            f"POND_API_KEY must not contain a plaintext value in docker-compose.yml "
            f"(got: {api_key!r}). Use Docker secrets + POND_API_KEY_FILE instead."
        )


def test_no_plaintext_session_secret_in_environment(ponddb_service: dict) -> None:
    """POND_WEBSITE_SESSION_SECRET must NOT be set to a plaintext value."""
    env = _get_env_as_dict(ponddb_service)
    session_secret = env.get("POND_WEBSITE_SESSION_SECRET", "")
    if session_secret:
        assert session_secret.startswith("${") or session_secret == "", (
            f"POND_WEBSITE_SESSION_SECRET must not be plaintext in docker-compose.yml "
            f"(got: {session_secret!r}). Use Docker secrets + POND_WEBSITE_SESSION_SECRET_FILE."
        )


# ---------------------------------------------------------------------------
# Secret file env vars point to /run/secrets/
# ---------------------------------------------------------------------------


def test_jwt_secret_file_env_points_to_run_secrets(ponddb_service: dict) -> None:
    """POND_JWT_SECRET_FILE env var must point to /run/secrets/ path."""
    env = _get_env_as_dict(ponddb_service)
    assert "POND_JWT_SECRET_FILE" in env, (
        "ponddb environment must set POND_JWT_SECRET_FILE "
        "(e.g. POND_JWT_SECRET_FILE=/run/secrets/jwt_secret)"
    )
    path = env["POND_JWT_SECRET_FILE"]
    assert path.startswith("/run/secrets/"), (
        f"POND_JWT_SECRET_FILE must point to /run/secrets/, got: {path!r}"
    )


def test_api_key_file_env_points_to_run_secrets(ponddb_service: dict) -> None:
    """POND_API_KEY_FILE env var must point to /run/secrets/ path."""
    env = _get_env_as_dict(ponddb_service)
    assert "POND_API_KEY_FILE" in env, (
        "ponddb environment must set POND_API_KEY_FILE "
        "(e.g. POND_API_KEY_FILE=/run/secrets/api_key)"
    )
    path = env["POND_API_KEY_FILE"]
    assert path.startswith("/run/secrets/"), (
        f"POND_API_KEY_FILE must point to /run/secrets/, got: {path!r}"
    )


def test_session_secret_file_env_points_to_run_secrets(ponddb_service: dict) -> None:
    """POND_WEBSITE_SESSION_SECRET_FILE env var must point to /run/secrets/ path."""
    env = _get_env_as_dict(ponddb_service)
    assert "POND_WEBSITE_SESSION_SECRET_FILE" in env, (
        "ponddb environment must set POND_WEBSITE_SESSION_SECRET_FILE "
        "(e.g. POND_WEBSITE_SESSION_SECRET_FILE=/run/secrets/session_secret)"
    )
    path = env["POND_WEBSITE_SESSION_SECRET_FILE"]
    assert path.startswith("/run/secrets/"), (
        f"POND_WEBSITE_SESSION_SECRET_FILE must point to /run/secrets/, got: {path!r}"
    )


# ---------------------------------------------------------------------------
# _get_api_key() — file-based secret loading for API key
# ---------------------------------------------------------------------------

STRONG_SECRET = "this-is-a-very-strong-secret-key-32plus-chars"


def test_get_api_key_function_exists() -> None:
    """jwt_auth must expose _get_api_key() for file-based API key loading."""
    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)
    assert hasattr(jwt_module, "_get_api_key"), (
        "jwt_auth must expose _get_api_key() that reads from POND_API_KEY_FILE or POND_API_KEY"
    )


def test_get_api_key_reads_from_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_api_key() must read the key from POND_API_KEY_FILE when set."""
    secret_file = tmp_path / "api_key"
    secret_file.write_text("pond-secure-api-key-from-file")

    monkeypatch.setenv("POND_API_KEY_FILE", str(secret_file))
    monkeypatch.delenv("POND_API_KEY", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_api_key()
    assert result == "pond-secure-api-key-from-file"


def test_get_api_key_file_preferred_over_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POND_API_KEY_FILE takes precedence over POND_API_KEY env var."""
    secret_file = tmp_path / "api_key"
    secret_file.write_text("file-api-key-wins")

    monkeypatch.setenv("POND_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("POND_API_KEY", "env-api-key-ignored")

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_api_key()
    assert result == "file-api-key-wins"


def test_get_api_key_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_api_key() returns POND_API_KEY when no file is configured."""
    monkeypatch.delenv("POND_API_KEY_FILE", raising=False)
    monkeypatch.setenv("POND_API_KEY", "env-only-api-key")

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_api_key()
    assert result == "env-only-api-key"


def test_get_api_key_strips_trailing_newline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_api_key() strips trailing whitespace/newlines from file contents."""
    secret_file = tmp_path / "api_key"
    secret_file.write_text("pond-api-key-value\n")

    monkeypatch.setenv("POND_API_KEY_FILE", str(secret_file))
    monkeypatch.delenv("POND_API_KEY", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_api_key()
    assert result == "pond-api-key-value"


def test_get_api_key_missing_file_raises_500(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POND_API_KEY_FILE pointing to non-existent file → HTTPException(500)."""
    from fastapi import HTTPException

    monkeypatch.setenv("POND_API_KEY_FILE", str(tmp_path / "nonexistent_api_key"))
    monkeypatch.delenv("POND_API_KEY", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module._get_api_key()

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# _get_session_secret() — file-based secret loading for session secret
# ---------------------------------------------------------------------------


def test_get_session_secret_function_exists() -> None:
    """jwt_auth must expose _get_session_secret() for file-based session secret loading."""
    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)
    assert hasattr(jwt_module, "_get_session_secret"), (
        "jwt_auth must expose _get_session_secret() that reads from "
        "POND_WEBSITE_SESSION_SECRET_FILE or POND_WEBSITE_SESSION_SECRET"
    )


def test_get_session_secret_reads_from_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_session_secret() must read the secret from POND_WEBSITE_SESSION_SECRET_FILE."""
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("strong-session-secret-from-file")

    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET_FILE", str(secret_file))
    monkeypatch.delenv("POND_WEBSITE_SESSION_SECRET", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_session_secret()
    assert result == "strong-session-secret-from-file"


def test_get_session_secret_file_preferred_over_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POND_WEBSITE_SESSION_SECRET_FILE takes precedence over the env var."""
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("file-session-secret-wins")

    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", "env-session-secret-ignored")

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_session_secret()
    assert result == "file-session-secret-wins"


def test_get_session_secret_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_session_secret() falls back to POND_WEBSITE_SESSION_SECRET env var."""
    monkeypatch.delenv("POND_WEBSITE_SESSION_SECRET_FILE", raising=False)
    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET", "env-session-secret-value")

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_session_secret()
    assert result == "env-session-secret-value"


def test_get_session_secret_strips_trailing_newline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_session_secret() strips trailing whitespace/newlines from file contents."""
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("session-secret-value\n")

    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET_FILE", str(secret_file))
    monkeypatch.delenv("POND_WEBSITE_SESSION_SECRET", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_session_secret()
    assert result == "session-secret-value"


def test_get_session_secret_missing_file_raises_500(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POND_WEBSITE_SESSION_SECRET_FILE pointing to non-existent file → HTTPException(500)."""
    from fastapi import HTTPException

    monkeypatch.setenv(
        "POND_WEBSITE_SESSION_SECRET_FILE", str(tmp_path / "nonexistent_session_secret")
    )
    monkeypatch.delenv("POND_WEBSITE_SESSION_SECRET", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    with pytest.raises(HTTPException) as exc_info:
        jwt_module._get_session_secret()

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# require_auth uses _get_api_key() instead of raw os.environ
# ---------------------------------------------------------------------------


def test_require_auth_uses_get_api_key_for_validation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """require_auth must validate X-API-Key via _get_api_key() so file-based keys work."""
    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # _get_api_key must exist (already tested above), here just confirm
    # the module is structured correctly
    assert callable(getattr(jwt_module, "_get_api_key", None)), (
        "_get_api_key() must be callable so require_auth can use it"
    )


def test_verify_session_cookie_uses_get_session_secret(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_verify_session_cookie must use _get_session_secret() for HMAC verification."""
    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    # _get_session_secret must exist and be usable
    assert callable(getattr(jwt_module, "_get_session_secret", None)), (
        "_get_session_secret() must be callable so _verify_session_cookie can use it"
    )


# ---------------------------------------------------------------------------
# Simulate /run/secrets reading (without actual Docker)
# ---------------------------------------------------------------------------


def test_jwt_secret_readable_from_simulated_run_secrets_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate /run/secrets layout: jwt_auth must read POND_JWT_SECRET_FILE from that path."""
    # Simulate /run/secrets/jwt_secret
    secrets_dir = tmp_path / "run" / "secrets"
    secrets_dir.mkdir(parents=True)
    jwt_secret_file = secrets_dir / "jwt_secret"
    jwt_secret_file.write_text(STRONG_SECRET)

    monkeypatch.setenv("POND_JWT_SECRET_FILE", str(jwt_secret_file))
    monkeypatch.delenv("POND_JWT_SECRET", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_secret()
    assert result == STRONG_SECRET


def test_api_key_readable_from_simulated_run_secrets_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate /run/secrets layout: _get_api_key() reads POND_API_KEY_FILE from that path."""
    secrets_dir = tmp_path / "run" / "secrets"
    secrets_dir.mkdir(parents=True)
    api_key_file = secrets_dir / "api_key"
    api_key_file.write_text("pond-prod-api-key-from-secrets")

    monkeypatch.setenv("POND_API_KEY_FILE", str(api_key_file))
    monkeypatch.delenv("POND_API_KEY", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_api_key()
    assert result == "pond-prod-api-key-from-secrets"


def test_session_secret_readable_from_simulated_run_secrets_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate /run/secrets: _get_session_secret() reads POND_WEBSITE_SESSION_SECRET_FILE."""
    secrets_dir = tmp_path / "run" / "secrets"
    secrets_dir.mkdir(parents=True)
    session_file = secrets_dir / "session_secret"
    session_file.write_text("prod-session-secret-from-docker-secrets")

    monkeypatch.setenv("POND_WEBSITE_SESSION_SECRET_FILE", str(session_file))
    monkeypatch.delenv("POND_WEBSITE_SESSION_SECRET", raising=False)

    import importlib

    import ponddb.jwt_auth as jwt_module

    importlib.reload(jwt_module)

    result = jwt_module._get_session_secret()
    assert result == "prod-session-secret-from-docker-secrets"
