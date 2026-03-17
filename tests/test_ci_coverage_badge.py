"""Tests for GitHub Actions CI workflow, coverage reporting, and README badge.

These tests define the DESIRED state for the CI task:
  - .github/workflows/ci.yml triggers only on push/PR to main
  - pytest step includes --cov for coverage reporting
  - pytest-cov is listed in pyproject.toml dev dependencies
  - README.md has a GitHub Actions CI badge

All tests should FAIL until the implementation is updated.
"""

import pathlib
import re
import tomllib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci() -> dict:
    assert CI_WORKFLOW.exists(), f"CI workflow not found at {CI_WORKFLOW}"
    with CI_WORKFLOW.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"CI workflow not found at {CI_WORKFLOW}"
    return CI_WORKFLOW.read_text()


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README.exists(), f"README.md not found at {README}"
    return README.read_text()


@pytest.fixture(scope="module")
def pyproject() -> dict:
    assert PYPROJECT.exists(), f"pyproject.toml not found at {PYPROJECT}"
    return tomllib.loads(PYPROJECT.read_text())


# ---------------------------------------------------------------------------
# Branch targeting — must run on push/PR to main specifically
# ---------------------------------------------------------------------------


def test_ci_push_targets_main_branch(ci: dict) -> None:
    """CI push trigger must target 'main' branch, not wildcard '**'.

    Triggering on all branches wastes CI minutes and adds noise.
    Only main needs the full test suite on every push; feature branches
    are covered by the pull_request trigger.
    """
    on = ci.get("on", ci.get(True, {}))
    push_cfg = on.get("push", {})
    branches = push_cfg.get("branches", []) if isinstance(push_cfg, dict) else []
    assert "main" in branches, (
        f"CI push trigger must list 'main' in branches, got: {branches!r}. "
        "Change 'branches: [\"**\"]' to 'branches: [main]'."
    )


def test_ci_push_does_not_trigger_on_all_branches(ci: dict) -> None:
    """CI push must NOT use the wildcard '**' that matches every branch.

    Using '**' triggers a full test run on every feature branch push,
    burning CI minutes and making the 'on: push' trigger meaningless.
    Use 'branches: [main]' instead and rely on pull_request for PRs.
    """
    on = ci.get("on", ci.get(True, {}))
    push_cfg = on.get("push", {})
    branches = push_cfg.get("branches", []) if isinstance(push_cfg, dict) else []
    assert "**" not in branches, (
        "CI push trigger must not use wildcard '**'. "
        "Replace with 'branches: [main]' to only run on main-branch pushes."
    )


def test_ci_pull_request_targets_main_branch(ci: dict) -> None:
    """CI pull_request trigger must target 'main', not wildcard '**'.

    Only PRs targeting main should run the full suite. PRs between
    feature branches don't need CI until they're ready to merge.
    """
    on = ci.get("on", ci.get(True, {}))
    pr_cfg = on.get("pull_request", {})
    branches = pr_cfg.get("branches", []) if isinstance(pr_cfg, dict) else []
    assert "main" in branches, (
        f"CI pull_request trigger must list 'main' in branches, got: {branches!r}. "
        "Change 'branches: [\"**\"]' to 'branches: [main]'."
    )


def test_ci_pull_request_does_not_trigger_on_all_branches(ci: dict) -> None:
    """CI pull_request must NOT use the wildcard '**'."""
    on = ci.get("on", ci.get(True, {}))
    pr_cfg = on.get("pull_request", {})
    branches = pr_cfg.get("branches", []) if isinstance(pr_cfg, dict) else []
    assert "**" not in branches, (
        "CI pull_request trigger must not use wildcard '**'. Replace with 'branches: [main]'."
    )


# ---------------------------------------------------------------------------
# Coverage reporting — pytest must run with --cov flag
# ---------------------------------------------------------------------------


def test_ci_pytest_step_includes_cov_flag(ci_text: str) -> None:
    """The pytest command in CI must include --cov to generate a coverage report.

    Without --cov, there is no signal on test coverage trends. Coverage data
    is especially useful in CI to catch regressions before merge.
    """
    assert "--cov" in ci_text, (
        "CI workflow pytest step must include --cov flag. "
        "Update the 'Run tests' step to: pytest tests/ --cov=src/ponddb --tb=short"
    )


def test_ci_pytest_step_includes_cov_report(ci_text: str) -> None:
    """Coverage report format must be specified (e.g. --cov-report=term-missing).

    Without --cov-report, pytest-cov defaults to an empty report that is hard
    to read in CI logs. 'term-missing' prints uncovered line numbers inline.
    """
    assert "--cov-report" in ci_text, (
        "CI workflow pytest step must include --cov-report flag. "
        "Add --cov-report=term-missing to show uncovered lines in CI output."
    )


def test_ci_pytest_step_targets_src_ponddb(ci_text: str) -> None:
    """--cov must point at the source package, not just the tests directory.

    'pytest tests/ --cov' without a path measures coverage of everything
    including test files themselves, inflating the percentage. Specify
    '--cov=src/ponddb' to measure only production code coverage.
    """
    assert "--cov=src/ponddb" in ci_text or "--cov src/ponddb" in ci_text, (
        "CI workflow --cov flag must target 'src/ponddb'. "
        "Use: pytest tests/ --cov=src/ponddb --cov-report=term-missing --tb=short"
    )


def test_ci_pytest_does_not_suppress_failures(ci_text: str) -> None:
    """pytest must not be followed by '|| true' or similar failure suppression.

    Suppressing the exit code defeats the purpose of CI — a failing test
    would show green, masking regressions until production.
    """
    # Find lines with pytest commands
    for line in ci_text.splitlines():
        if "pytest" in line and ("|| true" in line or "|| exit 0" in line):
            pytest.fail(f"pytest command must not suppress failures with '|| true': {line!r}")


# ---------------------------------------------------------------------------
# pyproject.toml — pytest-cov must be in dev dependencies
# ---------------------------------------------------------------------------


def test_pyproject_dev_deps_include_pytest_cov(pyproject: dict) -> None:
    """pytest-cov must be listed in [project.optional-dependencies.dev].

    The CI step 'pip install -e .[dev]' only installs what is declared here.
    Without pytest-cov in dev extras, '--cov' will raise a PluginNotFound error
    and the entire CI run will fail immediately on the dependency step.
    """
    dev_deps = pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    has_pytest_cov = any("pytest-cov" in dep for dep in dev_deps)
    assert has_pytest_cov, (
        f"pyproject.toml [project.optional-dependencies.dev] must include 'pytest-cov'. "
        f"Current dev deps: {dev_deps}. "
        "Add: 'pytest-cov>=5,<6'"
    )


def test_pyproject_dev_deps_have_pytest_cov_version_constraint(pyproject: dict) -> None:
    """pytest-cov dev dep must have a version constraint to prevent silent breakage.

    Pinning to a major version (e.g. >=5,<6) lets dependabot track updates
    while preventing a future incompatible release from silently breaking CI.
    """
    dev_deps = pyproject.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    cov_deps = [d for d in dev_deps if "pytest-cov" in d]
    assert cov_deps, "pytest-cov not found in dev deps (see test above)"
    dep = cov_deps[0]
    has_constraint = any(op in dep for op in [">=", "<=", "==", "~=", ">", "<"])
    assert has_constraint, (
        f"pytest-cov dep must have a version constraint, got: {dep!r}. "
        "Use 'pytest-cov>=5,<6' for semver-safe pinning."
    )


# ---------------------------------------------------------------------------
# README badge — GitHub Actions CI status badge
# ---------------------------------------------------------------------------


def test_readme_has_ci_badge(readme_text: str) -> None:
    """README must contain a GitHub Actions CI status badge.

    The badge gives contributors an instant visual signal of build health
    without navigating to the Actions tab. It should appear near the top
    of the README before the main content.
    """
    # GitHub Actions badge format: ![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)
    has_badge = (
        "actions/workflows/ci.yml/badge.svg" in readme_text
        or "github.com" in readme_text
        and "badge" in readme_text
        and "CI" in readme_text
    )
    assert has_badge, (
        "README.md must include a GitHub Actions CI badge near the top. "
        "Add: ![CI](https://github.com/pond-db/pond-db/actions/workflows/ci.yml/badge.svg)"
    )


def test_readme_ci_badge_links_to_actions(readme_text: str) -> None:
    """The CI badge in README must be a clickable link to the Actions workflow.

    A bare image tag shows the badge but clicking does nothing. Wrapping the
    image in a link lets contributors jump directly to the failing run.
    Expected format: [![CI](<badge-url>)](<actions-url>)
    """
    # Look for markdown link wrapping an image, or HTML <a><img> badge
    badge_link_pattern = re.compile(
        r"\[!\[.*?\]\(.*?badge.*?\)\]\(.*?actions.*?\)",
        re.IGNORECASE,
    )
    html_badge_pattern = re.compile(
        r'<a\s+href="[^"]*actions[^"]*"[^>]*>.*?<img\s+src="[^"]*badge[^"]*"',
        re.IGNORECASE | re.DOTALL,
    )
    assert badge_link_pattern.search(readme_text) or html_badge_pattern.search(readme_text), (
        "README CI badge must be a clickable link to the Actions page. "
        "Use: [![CI](<badge-url>)](<actions-run-url>) or <a href=actions><img src=badge></a>"
    )


def test_readme_ci_badge_near_top(readme_text: str) -> None:
    """The CI badge should appear in the first 10 lines of README.

    Contributors scan the top of a README first. A badge buried 50 lines
    in provides no value as a quick health signal.
    """
    lines = readme_text.splitlines()
    badge_line_idx = None
    for i, line in enumerate(lines[:20]):
        if "badge.svg" in line or ("badge" in line.lower() and "actions" in line.lower()):
            badge_line_idx = i
            break
    assert badge_line_idx is not None, (
        "CI badge not found in the first 20 lines of README.md. "
        "Move the badge to appear immediately after the # PondDB title."
    )


def test_readme_ci_badge_references_ci_yml(readme_text: str) -> None:
    """The CI badge URL must reference ci.yml specifically (not a generic workflow path).

    A badge pointing to a non-existent or renamed workflow shows 'no status'.
    The badge path must match the actual workflow filename.
    """
    assert "ci.yml" in readme_text, (
        "README CI badge URL must include 'ci.yml' to match the workflow filename. "
        "Badge URL format: .../workflows/ci.yml/badge.svg"
    )
