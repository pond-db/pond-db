# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""PondDB CLI — serve, check, and version commands.

Entry point: pond (registered in pyproject.toml as ponddb.cli:main)
"""

import os
import sys

import click

from ponddb import __version__

# ANSI helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


@click.group()
@click.version_option(version=__version__, prog_name="pond")
def main() -> None:
    """PondDB — serverless SQL analytics engine powered by DuckDB."""


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8432, type=int, help="Port number")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--workers", default=1, type=int, help="Number of worker processes")
def serve(host: str, port: int, reload: bool, workers: int) -> None:
    """Start the PondDB server."""
    try:
        import uvicorn
    except ImportError:
        click.echo("Error: uvicorn is required. Install with: pip install uvicorn", err=True)
        sys.exit(1)

    click.echo(f"Starting PondDB v{__version__} on {host}:{port}")
    uvicorn.run(
        "ponddb.app:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers,
    )


@main.command()
def version() -> None:
    """Show PondDB version."""
    click.echo(f"PondDB v{__version__}")


@main.command()
def check() -> None:
    """Validate environment configuration."""
    click.echo(f"PondDB v{__version__} — Environment Check\n")

    checks = [
        ("POND_API_KEY", True, "Master API key for authentication"),
        ("POND_JWT_SECRET", True, "JWT signing secret"),
        ("POND_WEBSITE_SESSION_SECRET", False, "Dashboard session cookie secret"),
        ("POND_GOOGLE_CLIENT_ID", False, "Google OAuth client ID"),
        ("POND_GOOGLE_CLIENT_SECRET", False, "Google OAuth client secret"),
        ("POND_GITHUB_CLIENT_ID", False, "GitHub OAuth client ID"),
        ("POND_GITHUB_CLIENT_SECRET", False, "GitHub OAuth client secret"),
        ("POND_OAUTH_SECRET", False, "OAuth HMAC state secret"),
        ("POND_SMTP_HOST", False, "SMTP server for invite emails"),
        ("POND_SMTP_USER", False, "SMTP username"),
        ("POND_SMTP_PASSWORD", False, "SMTP password"),
        ("POND_PONDAPI_RATE_LIMIT", False, "PondAPI rate limit (default: 10/min)"),
    ]

    all_ok = True
    for var, required, description in checks:
        value = os.environ.get(var, "")
        if value:
            masked = value[:3] + "*" * max(0, len(value) - 3)
            click.echo(f"  {GREEN}✓{RESET} {var} = {masked}")
        elif required:
            click.echo(f"  {RED}✗{RESET} {var} — {description} {RED}(REQUIRED){RESET}")
            all_ok = False
        else:
            click.echo(f"  {YELLOW}○{RESET} {var} — {description} (optional)")

    click.echo()
    if all_ok:
        click.echo(f"{GREEN}All required variables are set.{RESET}")
    else:
        click.echo(f"{RED}Missing required variables. See .env.example for details.{RESET}")
        sys.exit(1)
