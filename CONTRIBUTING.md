# Contributing to PondDB

Thanks for your interest in contributing to PondDB!

## Development setup

```bash
git clone https://github.com/DatabaseCompany/ponddb.git
cd ponddb
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

## Code style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Submitting changes

1. Fork the repo and create a feature branch from `main`
2. Add tests for any new functionality
3. Ensure all tests pass and linting is clean
4. Open a pull request with a clear description of the change

## Reporting issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- PondDB version (`pond --version` or check `/health`)
