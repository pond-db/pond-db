# PondDB

[![CI](https://github.com/DatabaseCompany/db-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/DatabaseCompany/db-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

Self-hosted SQL analytics engine powered by DuckDB. Serverless session management,
async query execution, multi-tenant isolation, and a full SaaS dashboard — all in a
single Python package.

## Features

- **DuckDB-backed sessions** — on-demand spin-up (<500ms), auto-suspend on idle, transparent resume
- **Async PondAPI** — submit SQL, poll for results, rate-limited per tenant
- **SQL sandbox** — 15 blocked patterns prevent file access, config changes, and extension loading
- **JWT + API key auth** — token exchange, refresh, revocation, and API key fallback
- **Multi-tenant isolation** — query store, history, and executions scoped per tenant
- **OAuth** — Google and GitHub SSO with HMAC CSRF protection
- **Invite system** — token-based invites with optional SMTP email delivery
- **Query store** — save, name, slug, public/private visibility, pagination
- **Share links** — execute saved queries via `/q/{slug}`, rate-limited per IP
- **Dataset manager** — CSV/Parquet upload, auto-registered as DuckDB tables on session create/resume
- **SQL editor** — CodeMirror 6, HTMX execution, schema sidebar with click-to-insert
- **SaaS dashboard** — Custom CSS, sidebar nav, stat cards, status badges
- **Admin console** — invites, namespaces, workgroup quotas, usage monitoring
- **Python SDK** — `DuckCloudClient` with auto-refresh, retry, session management
- **CLI** — `pond serve`, `pond version`, `pond check`
- **2,550+ tests**, 92% coverage

## Quickstart

### Docker (recommended)

```bash
cp .env.example .env
# Edit .env — set POND_API_KEY and POND_JWT_SECRET
docker compose up
```

### pip

```bash
pip install ponddb
export POND_API_KEY=changeme
export POND_JWT_SECRET=your-secret-here
pond serve
# Or: uvicorn ponddb.app:app --host 0.0.0.0 --port 8432
```

### Verify

```bash
curl http://localhost:8432/health
# {"status":"ok","version":"0.1.0","sessions":0}
```

## Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Default | Description |
|----------|---------|-------------|
| `POND_API_KEY` | *(required)* | Master API key for `/auth/token` |
| `POND_JWT_SECRET` | *(required)* | JWT HS256 signing secret |
| `POND_HOST` | `0.0.0.0` | Server bind host |
| `POND_PORT` | `8432` | Server bind port |
| `POND_IDLE_TIMEOUT` | `300` | Seconds of idle before auto-suspend |
| `POND_MAX_SESSION_AGE` | `86400` | Maximum session lifetime in seconds |
| `POND_DATA_ROOT` | `./data` | Root directory for dataset uploads |
| `POND_SQLITE_PATH` | `./ponddb.db` | Path to the SQLite metadata store |
| `POND_MAX_RESULT_MB` | `100` | Maximum query result size in MB |
| `POND_SESSION_MEMORY_LIMIT` | `2GB` | Per-session DuckDB memory cap |
| `POND_LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |

See [`.env.example`](.env.example) for OAuth, SMTP, and rate limit variables.

Run `pond check` to validate your environment.

## API Reference

### Core

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Server status, version, session count |
| POST | `/session` | No | Create a DuckDB session |
| DELETE | `/session/{id}` | No | Destroy a session |
| GET | `/sessions` | No | List active sessions |
| POST | `/query` | JWT | Execute SQL synchronously |
| POST | `/pondapi/execute` | JWT | Submit SQL for async execution |
| GET | `/pondapi/execute/{id}/result` | JWT | Poll async execution result |
| GET | `/schema?session_id=` | JWT | Table and column introspection |
| GET | `/history` | JWT | Query execution history |
| POST | `/catalog/mount` | JWT | Mount a local file into a session |
| GET | `/metrics` | No | Prometheus-compatible metrics endpoint |
| GET | `/editor` | No | Web-based SQL editor (HTML) |

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/token` | No | Exchange API key for JWT |
| POST | `/auth/refresh` | No | Refresh an access token |
| POST | `/auth/revoke` | JWT | Revoke a token |

### Data

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/datasets` | API Key | List uploaded datasets |
| POST | `/datasets` | API Key | Upload CSV/Parquet |
| DELETE | `/datasets/{name}` | API Key | Delete a dataset |
| POST | `/queries` | JWT | Save a named query |
| GET | `/queries` | JWT | List saved queries |
| GET | `/q/{slug}` | Optional | Execute a shared query |

### Admin (requires admin JWT)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/namespaces` | Create namespace |
| POST | `/workgroups` | Create workgroup with quota |
| POST | `/invites` | Create invite token |
| POST | `/invites/{token}/accept` | Accept invite (no auth) |

## Python SDK

```python
from ponddb import PondDB

# Connect with an API key
client = PondDB(base_url="http://localhost:8432", api_key="your-api-key")

# Use as a context manager — session is created and destroyed automatically
with client as session:
    result = session.query("SELECT 42 AS answer")
    print(result)  # {"columns": ["answer"], "rows": [[42]], ...}
```

Or use the lower-level SDK client:

```python
from sdk.duckcloud import DuckCloudClient

client = DuckCloudClient(
    base_url="http://localhost:8432",
    api_key="your-api-key",
)
client.authenticate()
client.create_session()
result = client.query("SELECT 42 AS answer")
client.destroy_session()
```

## CLI

```bash
pond serve              # Start the server
pond serve --port 9000  # Custom port
pond serve --reload     # Dev mode with auto-reload
pond version            # Print version
pond check              # Validate environment variables
```

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Clients                              │
│  (HTTP / Python SDK / CLI / SQL Editor)                │
└─────────────────────┬──────────────────────────────────┘
                      │
                      ▼
┌────────────────────────────────────────────────────────┐
│              FastAPI + Uvicorn                          │
│  14 routers: query, auth, session, pondapi, datasets,  │
│  queries, share, schema, invites, namespaces, oauth,   │
│  admin, htmx, health                                   │
└──────┬───────────────────────┬─────────────────────────┘
       │                       │
       ▼                       ▼
┌──────────────────┐   ┌─────────────────────────┐
│  Session Manager │   │    JWT Auth + Sandbox   │
│  (DuckDB pool,   │   │  (python-jose, 15      │
│   idle watchdog) │   │   blocked patterns)     │
└────────┬─────────┘   └─────────────────────────┘
         │
         ▼
┌──────────────────┐   ┌─────────────────────────┐
│  DuckDB Engine   │   │   SQLite Metadata       │
│  (one conn/sess, │   │  (queries, history,     │
│   sandboxed)     │   │   invites, datasets)    │
└──────────────────┘   └─────────────────────────┘
```

### Session lifecycle

```
COLD ──► ACTIVE ──► SUSPENDED ──► DESTROYED
           │                          ▲
           └──────────────────────────┘
              DELETE /session/{id}
```

- **COLD → ACTIVE**: `POST /session` creates a DuckDB connection (<500 ms)
- **ACTIVE → SUSPENDED**: idle watchdog fires after `POND_IDLE_TIMEOUT` seconds
- **SUSPENDED → ACTIVE**: next query triggers transparent resume (<300 ms)
- **ANY → DESTROYED**: `DELETE /session/{id}` or max age exceeded

> **Session Lifecycle:** PondDB automatically suspends idle sessions after 5 minutes
> and resumes them transparently when the next query arrives. Uploaded datasets are
> always available after resume. Temporary tables created with `CREATE TEMP TABLE`
> are lost on suspend — use uploaded datasets for persistent data.

### Workgroup quotas

When `max_concurrent_sessions` is set on a workgroup, PondDB enforces it at session
creation time. If the limit is reached and a suspended session exists, PondDB will
resume it instead of rejecting the request — smart scheduling with zero wasted resources.

## Development

```bash
git clone https://github.com/DatabaseCompany/db-engine.git
cd db-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Run stress tests
pytest tests/test_stress_*.py -v

# Lint
ruff check src/ tests/

# Demo
python scripts/demo_data.py          # Generate sample CSVs
python scripts/demo.py --api-key=changeme  # Run full demo (requires running server)
```

## Secret Management

PondDB uses `POND_JWT_SECRET` to sign JWT tokens and `POND_API_KEY` for API key auth.
Keep both out of version control.

### Rotating the JWT secret (zero-downtime)

Use `scripts/rotate_jwt_secret.sh` for zero-downtime rotation. After running:

```
POND_JWT_SECRET=<new-64-char-hex-secret>   # used to sign new tokens
POND_JWT_SECRET_V1=<old-secret>            # accepted during rollover
```

| Variable | Description |
|---|---|
| `POND_JWT_SECRET` | Active signing secret (required) |
| `POND_JWT_SECRET_V1` | Previous secret — accepted during rotation rollover |
| `POND_ENV_FILE` | Override path for the `.env` file used by `rotate_jwt_secret.sh` |
| `POND_AUDIT_LOG` | Override path for the rotation audit log |

### Secret scanning

This repo uses [detect-secrets](https://github.com/Yelp/detect-secrets) as a pre-commit
hook. Install with:

```bash
pip install pre-commit detect-secrets
pre-commit install
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR guidelines.

## License

[MIT](LICENSE) — DatabaseCompany
