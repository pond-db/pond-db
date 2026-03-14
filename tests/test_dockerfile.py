"""Tests for Dockerfile structure and correctness.

These tests parse the Dockerfile as text and assert configuration requirements.
They do NOT build the image — just validate the source file.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile_lines() -> list[str]:
    assert DOCKERFILE.exists(), "Dockerfile not found at repo root"
    return DOCKERFILE.read_text().splitlines()


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    assert DOCKERFILE.exists(), "Dockerfile not found at repo root"
    return DOCKERFILE.read_text()


# ---------------------------------------------------------------------------
# Base image
# ---------------------------------------------------------------------------


def test_dockerfile_uses_python_312(dockerfile_text: str) -> None:
    assert "python:3.12" in dockerfile_text


def test_dockerfile_uses_slim_image(dockerfile_text: str) -> None:
    """Use slim (or smaller) variant to minimise image size."""
    assert "slim" in dockerfile_text or "alpine" in dockerfile_text


# ---------------------------------------------------------------------------
# Multi-stage build
# ---------------------------------------------------------------------------


def test_dockerfile_has_multi_stage_build(dockerfile_lines: list[str]) -> None:
    """Must use multi-stage build: a 'builder' stage and a final runtime stage."""
    from_lines = [l for l in dockerfile_lines if l.strip().upper().startswith("FROM")]
    assert len(from_lines) >= 2, (
        f"Expected at least 2 FROM statements for multi-stage build, found {len(from_lines)}: {from_lines}"
    )


def test_dockerfile_builder_stage_named(dockerfile_text: str) -> None:
    """First FROM must be named (e.g. 'FROM ... AS builder')."""
    import re

    first_from = next(
        l for l in dockerfile_text.splitlines() if l.strip().upper().startswith("FROM")
    )
    assert re.search(r"\bAS\b", first_from, re.IGNORECASE), (
        f"First FROM stage must be named with AS: {first_from!r}"
    )


# ---------------------------------------------------------------------------
# Port exposure
# ---------------------------------------------------------------------------


def test_dockerfile_exposes_8432(dockerfile_text: str) -> None:
    assert "EXPOSE 8432" in dockerfile_text


# ---------------------------------------------------------------------------
# CMD / entrypoint
# ---------------------------------------------------------------------------


def test_dockerfile_cmd_uses_uvicorn(dockerfile_text: str) -> None:
    assert "uvicorn" in dockerfile_text


def test_dockerfile_cmd_uses_ponddb_app(dockerfile_text: str) -> None:
    assert "ponddb.app:app" in dockerfile_text


def test_dockerfile_cmd_binds_to_0000(dockerfile_text: str) -> None:
    assert "0.0.0.0" in dockerfile_text


def test_dockerfile_cmd_uses_port_8432(dockerfile_text: str) -> None:
    assert "8432" in dockerfile_text


# ---------------------------------------------------------------------------
# Security / best practices
# ---------------------------------------------------------------------------


def test_dockerfile_has_non_root_user(dockerfile_text: str) -> None:
    """Container must not run as root — set a USER directive."""
    assert "USER" in dockerfile_text, "Dockerfile must set a non-root USER"


def test_dockerfile_has_workdir(dockerfile_text: str) -> None:
    assert "WORKDIR" in dockerfile_text


def test_dockerfile_copies_source(dockerfile_text: str) -> None:
    """Source files must be copied into the image."""
    assert "COPY" in dockerfile_text


def test_dockerfile_installs_package(dockerfile_text: str) -> None:
    """pip install must run inside the image build."""
    assert "pip install" in dockerfile_text


# ---------------------------------------------------------------------------
# .dockerignore exists
# ---------------------------------------------------------------------------


def test_dockerignore_exists() -> None:
    dockerignore = REPO_ROOT / ".dockerignore"
    assert dockerignore.exists(), ".dockerignore missing — prevents bloated build context"


def test_dockerignore_excludes_dotenv(dockerfile_text: str) -> None:
    """Secrets must never enter the build context."""
    dockerignore = REPO_ROOT / ".dockerignore"
    if dockerignore.exists():
        content = dockerignore.read_text()
        assert ".env" in content, ".dockerignore must exclude .env files"
