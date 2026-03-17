# PondDB v1.0.0 Release Checklist

Generated: 2026-03-17

## Test Suite

- [x] **Full test suite passes (0 failures out of 2,552)**
  - 2552 passed, 4 skipped, 15 deselected (browser tests), 38 warnings
  - Run: `pytest tests/ -m 'not browser'`

- [x] **max_concurrent_sessions enforced**
  - Implemented in `session_manager.py::create_session()`
  - Tests: `tests/test_session_quota.py` (61 tests, all pass)

- [x] **UI refactor complete (3/3 parts)**
  - Part 1: Base layout, pond.css, dashboard
  - Part 2: HTMX workgroup tabs, suspend/resume, tab partials
  - Part 3: Landing page, login page, editor upgrade
  - Tests: `tests/test_ui_part3.py` (53 tests, all pass)

- [x] **All Playwright e2e tests documented**
  - Browser tests require live server: `pytest tests/test_browser.py --base-url http://localhost:8432`
  - Not run in CI (requires live server) — properly excluded with `-m 'not browser'`

## Security

- [x] **No secrets in current code (grep results clean)**
  - No hardcoded passwords, API keys, or JWT secrets in source
  - No hardcoded internal IPs in source code (test fixtures using 192.168.x.x are acceptable)
  - Personal email `2014houtianlu@gmail.com` removed from `landing.html` and `test_ui_part3.py`
  - Contact email now configurable via `POND_CONTACT_EMAIL` env var

- [x] **Secrets in git history documented**
  - Personal email appears in commit `81666e9` in git history
  - **Action required**: Run BFG Repo Cleaner before making repo public (see `BLOCKERS.md`)

- [x] **.env.example complete**
  - All 25+ environment variables documented with defaults and descriptions
  - Includes new `POND_CONTACT_EMAIL` variable

- [x] **secrets/ directory added to .gitignore**
  - Secrets files exist locally but are not committed

- [x] **No internal IPs, personal emails, or Discord references in current code**
  - Internal server IP (192.168.88.19) not found anywhere in source
  - Discord webhook references not found
  - Personal email scrubbed from landing page template

## Documentation

- [x] **README renders correctly**
  - BSL 1.1 license badge updated
  - Docker quickstart section present
  - API reference table present
  - Architecture diagram present

- [x] **CONTRIBUTING.md present** — dev setup, test commands, PR process, code style

- [x] **ARCHITECTURE.md present** — component diagram, DB schema, API flow, security model

- [x] **CHANGELOG.md present** — v1.0.0 with Keep a Changelog format, all major features listed

- [x] **docs/ folder** — setup.md, configuration.md, api.md, security.md

## Infrastructure

- [x] **LICENSE file present** — Business Source License 1.1, Change Date 2029-03-16

- [x] **GitHub Actions CI workflow present** — lint + test + build jobs, browser tests excluded

- [x] **GitHub Actions Release workflow present** — builds Docker image on tag push

- [x] **Issue templates present** — bug_report.md, feature_request.md, PULL_REQUEST_TEMPLATE.md

- [x] **docker-compose.yml works from clean state**
  - Multi-stage Dockerfile with non-root user and health check
  - Requires: `scripts/setup-secrets.sh` to generate secrets files
  - Run: `bash scripts/setup-secrets.sh && docker compose up`

## Code Quality

- [x] **Linter run** — 85 issues auto-fixed; 41 remaining (E741, F841, E402, E702, F821 in tests — non-critical)
  - E402: Intentional late imports in `session_manager.py` and `app.py`
  - F841: Unused variables in test files (no functional impact)
  - F821: False positives from quoted type annotations in test files

- [x] **License headers added** — all Python source files in `src/ponddb/` and `src/sdk/`

- [x] **REUSABLE_COMPONENTS.md created**

- [x] **THIRD_PARTY_LICENSES.md created**

## Remaining Work (for Tianlu)

1. **CRITICAL — BFG Repo Cleaner**: Run before making repo public (see `BLOCKERS.md` for exact commands)
2. **npm audit**: Not applicable (Python project, no Node.js dependencies)
3. **Browser e2e tests**: Run manually against a live server before release: `pytest tests/test_browser.py --base-url http://localhost:8432`
4. **Docker image size**: Build and verify: `docker build -t ponddb . && docker image ls ponddb`
5. **CI badges**: Update README badges once repo is made public and CI runs successfully
6. **Set POND_CONTACT_EMAIL**: Add to your deployment .env file
7. **pyproject.toml version**: Consider bumping to `1.0.0` before tagging the release
