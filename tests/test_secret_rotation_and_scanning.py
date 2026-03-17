"""Tests for secret management tooling.

Covers:
- scripts/rotate_jwt_secret.sh: generates strong new secret, promotes old to V1,
  writes audit log entry, updates .env file atomically
- .pre-commit-config.yaml: exists, includes detect-secrets hook pointing at baseline
- .secrets.baseline: exists, valid JSON, has detect-secrets format
- README: has a "Secret Management" section with rotation instructions
- detect-secrets scan: injected test secret detected; clean file passes
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths relative to repo root
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "rotate_jwt_secret.sh"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
SECRETS_BASELINE = REPO_ROOT / ".secrets.baseline"
README = REPO_ROOT / "README.md"


# ---------------------------------------------------------------------------
# Helper: skip detect-secrets tests gracefully if the tool is absent
# ---------------------------------------------------------------------------


def _detect_secrets_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "--version"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


# ===========================================================================
# 1. scripts/rotate_jwt_secret.sh — existence and permissions
# ===========================================================================


def test_rotation_script_exists() -> None:
    """scripts/rotate_jwt_secret.sh must exist."""
    assert SCRIPT_PATH.exists(), (
        f"Expected rotation script at {SCRIPT_PATH} — create scripts/rotate_jwt_secret.sh"
    )


def test_rotation_script_is_executable() -> None:
    """scripts/rotate_jwt_secret.sh must be executable."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"
    mode = SCRIPT_PATH.stat().st_mode
    assert bool(mode & stat.S_IXUSR), f"{SCRIPT_PATH} must have execute permission (chmod +x)"


def test_rotation_script_has_bash_shebang() -> None:
    """Rotation script must start with a bash shebang."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"
    first_line = SCRIPT_PATH.read_text().splitlines()[0]
    assert first_line.startswith("#!/"), (
        "rotate_jwt_secret.sh must have a shebang line (e.g. #!/usr/bin/env bash)"
    )
    assert "bash" in first_line, "Shebang must reference bash"


# ===========================================================================
# 2. scripts/rotate_jwt_secret.sh — behavior
# ===========================================================================


def test_rotation_script_generates_new_secret(tmp_path: Path) -> None:
    """Running the script must produce a NEW_SECRET value that differs from the old one."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    old_secret = "old-secret-value-for-testing-rotation-32chars"
    env_file = tmp_path / ".env"
    env_file.write_text(f"POND_JWT_SECRET={old_secret}\n")
    audit_log = tmp_path / "rotation_audit.log"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(env_file),
        "POND_AUDIT_LOG": str(audit_log),
        "POND_DRY_RUN": "0",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, (
        f"Rotation script failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # New .env must contain a POND_JWT_SECRET different from the old one
    new_env_text = env_file.read_text()
    assert "POND_JWT_SECRET=" in new_env_text, "Updated .env must contain POND_JWT_SECRET=..."
    # Extract new secret value
    for line in new_env_text.splitlines():
        if line.startswith("POND_JWT_SECRET="):
            new_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
            assert new_secret != old_secret, (
                "Rotation must produce a NEW secret different from the old one"
            )
            assert len(new_secret) >= 32, (
                f"New secret must be at least 32 chars (got {len(new_secret)})"
            )
            break
    else:
        pytest.fail("No POND_JWT_SECRET= line found in updated .env")


def test_rotation_script_promotes_old_secret_to_v1(tmp_path: Path) -> None:
    """Old secret must be written as POND_JWT_SECRET_V1 to allow token fallback."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    old_secret = "my-old-jwt-secret-for-rotation-testing-32chr"
    env_file = tmp_path / ".env"
    env_file.write_text(f"POND_JWT_SECRET={old_secret}\n")
    audit_log = tmp_path / "rotation_audit.log"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(env_file),
        "POND_AUDIT_LOG": str(audit_log),
        "POND_DRY_RUN": "0",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    new_env_text = env_file.read_text()
    assert "POND_JWT_SECRET_V1=" in new_env_text, (
        "Rotation must preserve old secret as POND_JWT_SECRET_V1 for token fallback"
    )
    for line in new_env_text.splitlines():
        if line.startswith("POND_JWT_SECRET_V1="):
            v1_value = line.split("=", 1)[1].strip().strip('"').strip("'")
            assert v1_value == old_secret, (
                f"POND_JWT_SECRET_V1 must equal old secret. "
                f"Expected {old_secret!r}, got {v1_value!r}"
            )


def test_rotation_script_writes_audit_event(tmp_path: Path) -> None:
    """Rotation must append a structured audit event to the audit log."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    old_secret = "secret-being-rotated-for-audit-test-32chars"
    env_file = tmp_path / ".env"
    env_file.write_text(f"POND_JWT_SECRET={old_secret}\n")
    audit_log = tmp_path / "rotation_audit.log"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(env_file),
        "POND_AUDIT_LOG": str(audit_log),
        "POND_DRY_RUN": "0",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    assert audit_log.exists(), (
        f"Rotation script must write an audit event to POND_AUDIT_LOG={audit_log}"
    )
    audit_contents = audit_log.read_text()
    assert audit_contents.strip(), "Audit log must not be empty after rotation"

    # Must contain a timestamp-like or event-like marker
    lower = audit_contents.lower()
    assert any(kw in lower for kw in ("rotate", "jwt", "secret", "rotation", "event")), (
        f"Audit log must mention rotation/jwt/secret. Got: {audit_contents!r}"
    )


def test_rotation_script_audit_entry_contains_timestamp(tmp_path: Path) -> None:
    """Audit log entries must include an ISO-8601-like timestamp."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    env_file = tmp_path / ".env"
    env_file.write_text("POND_JWT_SECRET=old-secret-for-timestamp-test-32plus\n")
    audit_log = tmp_path / "rotation_audit.log"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(env_file),
        "POND_AUDIT_LOG": str(audit_log),
        "POND_DRY_RUN": "0",
    }
    subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )

    if not audit_log.exists():
        pytest.fail("Audit log not created")

    contents = audit_log.read_text()
    # ISO-8601 date pattern like 2026-03-16 or 2026-03-16T12:00:00
    assert re.search(r"\d{4}-\d{2}-\d{2}", contents), (
        "Audit log must contain an ISO-8601 date (YYYY-MM-DD). Got: " + repr(contents)
    )


def test_rotation_script_new_secret_meets_minimum_entropy(tmp_path: Path) -> None:
    """Generated secret must be at least 32 chars and not obviously weak."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    env_file = tmp_path / ".env"
    env_file.write_text("POND_JWT_SECRET=old-value-entropy-check-test-32chars\n")
    audit_log = tmp_path / "rotation_audit.log"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(env_file),
        "POND_AUDIT_LOG": str(audit_log),
        "POND_DRY_RUN": "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    for line in env_file.read_text().splitlines():
        if line.startswith("POND_JWT_SECRET="):
            new_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
            assert len(new_secret) >= 32, (
                f"Generated secret must be ≥32 chars for adequate entropy (got {len(new_secret)})"
            )
            # Should not be the same value repeated
            unique_chars = set(new_secret)
            assert len(unique_chars) >= 8, (
                f"Generated secret must have at least 8 unique characters (got {len(unique_chars)})"
            )
            return
    pytest.fail("POND_JWT_SECRET not found in updated .env")


def test_rotation_script_missing_env_file_fails_gracefully(tmp_path: Path) -> None:
    """Script with non-existent POND_ENV_FILE must exit non-zero with a clear error."""
    assert SCRIPT_PATH.exists(), "Rotation script missing"

    env = {
        **os.environ,
        "POND_ENV_FILE": str(tmp_path / "nonexistent.env"),
        "POND_AUDIT_LOG": str(tmp_path / "audit.log"),
        "POND_DRY_RUN": "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode != 0, "Script must exit non-zero when POND_ENV_FILE does not exist"
    combined = result.stdout + result.stderr
    assert combined.strip(), "Script must emit an error message on failure"


# ===========================================================================
# 3. .pre-commit-config.yaml
# ===========================================================================


def test_pre_commit_config_exists() -> None:
    """.pre-commit-config.yaml must exist at the repo root."""
    assert PRE_COMMIT_CONFIG.exists(), (
        "Missing .pre-commit-config.yaml — create it with detect-secrets hook"
    )


def test_pre_commit_config_is_valid_yaml() -> None:
    """pre-commit config must be parseable YAML."""
    yaml = pytest.importorskip("yaml", reason="pyyaml not installed")
    assert PRE_COMMIT_CONFIG.exists(), ".pre-commit-config.yaml missing"
    content = PRE_COMMIT_CONFIG.read_text()
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict), "pre-commit config must be a YAML mapping"
    assert "repos" in parsed, "pre-commit config must have a 'repos' key"


def test_pre_commit_config_contains_detect_secrets_hook() -> None:
    """.pre-commit-config.yaml must reference detect-secrets."""
    assert PRE_COMMIT_CONFIG.exists(), ".pre-commit-config.yaml missing"
    content = PRE_COMMIT_CONFIG.read_text()
    assert "detect-secrets" in content, (
        ".pre-commit-config.yaml must include the detect-secrets hook"
    )


def test_pre_commit_config_references_secrets_baseline() -> None:
    """detect-secrets hook must point at .secrets.baseline via args."""
    assert PRE_COMMIT_CONFIG.exists(), ".pre-commit-config.yaml missing"
    content = PRE_COMMIT_CONFIG.read_text()
    assert ".secrets.baseline" in content, (
        "pre-commit detect-secrets hook must reference .secrets.baseline via --baseline arg"
    )


# ===========================================================================
# 4. .secrets.baseline
# ===========================================================================


def test_secrets_baseline_exists() -> None:
    """.secrets.baseline must exist at the repo root."""
    assert SECRETS_BASELINE.exists(), (
        "Missing .secrets.baseline — run: detect-secrets scan > .secrets.baseline"
    )


def test_secrets_baseline_is_valid_json() -> None:
    """.secrets.baseline must be valid JSON."""
    assert SECRETS_BASELINE.exists(), ".secrets.baseline missing"
    content = SECRETS_BASELINE.read_text()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        pytest.fail(f".secrets.baseline is not valid JSON: {exc}")
    assert isinstance(data, dict), ".secrets.baseline must be a JSON object"


def test_secrets_baseline_has_detect_secrets_format() -> None:
    """.secrets.baseline must have the detect-secrets version and results keys."""
    assert SECRETS_BASELINE.exists(), ".secrets.baseline missing"
    data = json.loads(SECRETS_BASELINE.read_text())
    assert "version" in data, (
        ".secrets.baseline must contain a 'version' key (detect-secrets format)"
    )
    assert "results" in data, (
        ".secrets.baseline must contain a 'results' key (detect-secrets format)"
    )


def test_secrets_baseline_version_is_string() -> None:
    """.secrets.baseline version field must be a non-empty string."""
    assert SECRETS_BASELINE.exists(), ".secrets.baseline missing"
    data = json.loads(SECRETS_BASELINE.read_text())
    version = data.get("version", "")
    assert isinstance(version, str) and version, (
        ".secrets.baseline 'version' must be a non-empty string"
    )


# ===========================================================================
# 5. README — Secret Management section
# ===========================================================================


def test_readme_has_secret_management_section() -> None:
    """README.md must contain a Secret Management heading."""
    assert README.exists(), "README.md missing"
    content = README.read_text()
    # Match ## Secret Management or ### Secret Management (case-insensitive)
    assert re.search(r"#+\s+secret\s+management", content, re.IGNORECASE), (
        "README.md must have a '## Secret Management' (or similar heading) section"
    )


def test_readme_secret_section_mentions_rotation() -> None:
    """Secret Management section must explain how to rotate the JWT secret."""
    assert README.exists(), "README.md missing"
    content = README.read_text()
    lower = content.lower()
    assert "rotat" in lower, "README Secret Management section must mention rotation (rotat...)"
    assert "rotate_jwt_secret" in lower or "rotate_jwt_secret.sh" in lower, (
        "README must reference the rotate_jwt_secret.sh script"
    )


def test_readme_secret_section_mentions_detect_secrets() -> None:
    """README Secret Management section must mention detect-secrets for pre-commit scanning."""
    assert README.exists(), "README.md missing"
    content = README.read_text()
    assert "detect-secrets" in content, (
        "README Secret Management section must mention detect-secrets"
    )


def test_readme_secret_section_explains_versioned_env_vars() -> None:
    """README must document the V1/V2 versioned secret env vars."""
    assert README.exists(), "README.md missing"
    content = README.read_text()
    assert "POND_JWT_SECRET_V2" in content or "POND_JWT_SECRET_V1" in content, (
        "README Secret Management section must document POND_JWT_SECRET_V1 / V2 "
        "for zero-downtime rotation"
    )


# ===========================================================================
# 6. detect-secrets scan behaviour
# ===========================================================================


@pytest.mark.skipif(
    not _detect_secrets_available(),
    reason="detect-secrets not installed — add to dev dependencies",
)
def test_detect_secrets_finds_injected_secret(tmp_path: Path) -> None:
    """detect-secrets scan must flag a file that contains a hard-coded JWT secret."""
    # Inject a realistic-looking secret into a temp Python file
    target = tmp_path / "config_with_leak.py"
    target.write_text(
        "# This file intentionally contains a leaked secret for testing\n"
        'JWT_SECRET = "supersecretvalue1234567890abcdef"\n'
    )

    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"detect-secrets scan failed: {result.stderr}"

    output = json.loads(result.stdout)
    # results is a dict of filepath → list of findings
    all_findings = []
    for findings in output.get("results", {}).values():
        all_findings.extend(findings)

    assert len(all_findings) > 0, (
        "detect-secrets must detect the injected secret in config_with_leak.py. "
        f"Scan output: {result.stdout}"
    )


@pytest.mark.skipif(
    not _detect_secrets_available(),
    reason="detect-secrets not installed — add to dev dependencies",
)
def test_detect_secrets_clean_file_has_no_findings(tmp_path: Path) -> None:
    """detect-secrets scan of a clean file must produce zero findings."""
    clean_file = tmp_path / "clean_module.py"
    clean_file.write_text(
        '"""A module with no hard-coded secrets."""\n\n'
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}!"\n'
    )

    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", str(clean_file)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"detect-secrets scan failed: {result.stderr}"

    output = json.loads(result.stdout)
    all_findings = []
    for findings in output.get("results", {}).values():
        all_findings.extend(findings)

    assert len(all_findings) == 0, (
        f"Clean file must produce zero detect-secrets findings, got: {all_findings}"
    )


@pytest.mark.skipif(
    not _detect_secrets_available(),
    reason="detect-secrets not installed — add to dev dependencies",
)
def test_detect_secrets_repo_scan_passes_with_baseline() -> None:
    """Running detect-secrets audit against the repo with its baseline must pass."""
    assert SECRETS_BASELINE.exists(), ".secrets.baseline missing"

    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", "--baseline", str(SECRETS_BASELINE)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    # Exit 0 means no new unaudited secrets beyond the baseline
    assert result.returncode == 0, (
        f"detect-secrets scan found new unaudited secrets beyond .secrets.baseline.\n"
        f"Run: detect-secrets scan --baseline .secrets.baseline\n"
        f"stderr: {result.stderr}"
    )


@pytest.mark.skipif(
    not _detect_secrets_available(),
    reason="detect-secrets not installed — add to dev dependencies",
)
def test_detect_secrets_version_matches_baseline(tmp_path: Path) -> None:
    """The installed detect-secrets version should match the baseline file version."""
    assert SECRETS_BASELINE.exists(), ".secrets.baseline missing"

    baseline_data = json.loads(SECRETS_BASELINE.read_text())
    baseline_version = baseline_data.get("version", "")

    result = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    installed_version = (result.stdout + result.stderr).strip()

    assert baseline_version, ".secrets.baseline must have a non-empty 'version'"
    assert installed_version, "detect-secrets --version produced no output"
    # Versions should match (at least major.minor)
    assert baseline_version.split(".")[:2] == installed_version.split(".")[:2], (
        f"detect-secrets baseline version {baseline_version!r} does not match "
        f"installed version {installed_version!r}. Regenerate baseline."
    )
