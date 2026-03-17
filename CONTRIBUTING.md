# Contributing to PondDB

Thank you for your interest in contributing to PondDB!

## Development Setup

```bash
git clone https://github.com/pond-db/pond-db.git
cd pond-db
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Install Playwright browsers for UI tests:

```bash
playwright install chromium
```

## Running Tests

```bash
# Full test suite (excludes browser tests that need a live server)
pytest tests/ -m 'not browser'

# Single file
pytest tests/test_session_lifecycle.py -v

# With coverage
pytest tests/ -m 'not browser' --cov=src/ponddb --cov-report=term-missing

# Browser/UI tests (requires running server at localhost:8432)
pytest tests/test_browser.py --base-url http://localhost:8432

# Stress tests
pytest tests/test_stress_*.py -v
```

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff check src/ tests/ --fix
ruff format src/ tests/
```

## Project Structure

```
src/ponddb/          # Main application package
  app.py             # FastAPI application and route registration
  session_manager.py # DuckDB session lifecycle
  metadata_store.py  # SQLite async metadata layer
  jwt_auth.py        # JWT authentication middleware
  templates/         # Jinja2 HTML templates
  static/            # CSS and static assets
src/sdk/             # Legacy Python SDK (duckcloud compat)
tests/               # pytest test suite (79 files, 2,550+ tests)
scripts/             # Demo and utility scripts
docs/                # Documentation
```

## Adding Features

1. Read `ARCHITECTURE.md` to understand the system
2. Write tests first (TDD preferred)
3. Keep source files under 200 lines — split if needed
4. Add type hints to all function signatures
5. Handle errors from external calls (DuckDB, SQLite, filesystem)

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Use [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat:` new feature, `fix:` bug fix, `docs:` docs only
   - `refactor:` no behavior change, `test:` test changes
3. Ensure all tests pass: `pytest tests/ -m 'not browser'`
4. Ensure linter is clean: `ruff check src/ tests/`
5. Update `CHANGELOG.md` under `[Unreleased]`
6. Open a pull request — use the PR template

## Reporting Issues

Open a GitHub issue with steps to reproduce, expected vs actual behavior, and PondDB version.

## Security Issues

Do not open public issues for security vulnerabilities. Email maintainers directly.

## License

By contributing, you agree your contributions are licensed under BSL 1.1. See [LICENSE](LICENSE).
