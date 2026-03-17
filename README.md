<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/pond-db/pond-db/main/.github/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/pond-db/pond-db/main/.github/logo-light.svg">
    <img alt="PondDB" src="https://raw.githubusercontent.com/pond-db/pond-db/main/.github/logo-light.svg" height="80">
  </picture>
</p>

<h3 align="center">Share DuckDB with your team — or let your AI agents query it.</h3>

<p align="center">
  <a href="https://github.com/pond-db/pond-db/actions/workflows/ci.yml"><img src="https://github.com/pond-db/pond-db/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/tests-2%2C580%20passing-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-92%25-brightgreen" alt="Coverage">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
</p>

[![CI](https://github.com/pond-db/pond-db/actions/workflows/ci.yml/badge.svg)](https://github.com/pond-db/pond-db/actions/workflows/ci.yml)

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#why-ponddb">Why PondDB</a> ·
  <a href="#for-ai-agents">For AI Agents</a> ·
  <a href="docs/api.md">API Reference</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

PondDB turns DuckDB into a multi-user analytics platform you run on your own hardware.
Upload data, share queries, manage team access — all from a browser or HTTP API. No cloud account needed.

> *"Like MotherDuck, but self-hosted. Like giving your AI agent a SQL brain."*

## Why PondDB?

DuckDB is incredible for solo analytics, but it's single-player — one file, one process, one person. When your team or your AI agents need to:

- **Query shared datasets** without emailing CSV files
- **Run SQL from a browser, script, or AI agent** via HTTP
- **Control who can access what data**
- **Auto-manage compute** (suspend idle, resume on demand)

...you need a server layer. MotherDuck does this in the cloud (and costs money). **PondDB does it on your machine, for free.**

## How it compares

|  | DuckDB | MotherDuck | **PondDB** |
|--|--------|-----------|-----------|
| Multi-user | ❌ Single file | ✅ Cloud | ✅ Self-hosted |
| Browser SQL editor | ❌ | ✅ | ✅ |
| HTTP query API | ❌ | ❌ | ✅ PondAPI |
| AI agent friendly | ❌ | ❌ | ✅ MCP + HTTP |
| Share queries | ❌ | ✅ | ✅ Share links |
| Upload CSV/Parquet | CLI only | ✅ | ✅ |
| Team access control | ❌ | ✅ | ✅ OAuth + workgroups |
| Session auto-management | ❌ | ✅ | ✅ Suspend/resume |
| Data stays on your machine | ✅ | ❌ | ✅ |
| Self-hosted | Embedded | ❌ | ✅ Docker |
| Price | Free | Free tier → paid | **Free forever** |

## Quickstart

```bash
git clone https://github.com/pond-db/pond-db && cd pond-db
cp .env.example .env    # set POND_API_KEY and POND_JWT_SECRET
docker compose up -d
```

Open `http://localhost:8432` → sign in → upload a CSV → run SQL. Done.

Verify it's running:

```bash
curl http://localhost:8432/health
# → {"status": "ok", "version": "1.0.0", "sessions": 0}
```

Or install with pip (development):

```bash
pip install ponddb
uvicorn ponddb.app:app --host 0.0.0.0 --port 8432
```

## Python SDK

```python
from ponddb import PondClient

client = PondClient("http://localhost:8432", api_key="pk_...")

with client as session:
    result = session.query("SELECT region, SUM(revenue) FROM sales GROUP BY 1")
    print(result.rows)
    # [{"region": "West", "revenue": 84210}, ...]
```

Or use curl:

```bash
# Submit query (returns instantly)
curl -X POST http://localhost:8432/pondapi/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT 42 as answer"}'
# {"execution_id": "abc-123", "status": "running"}

# Get results
curl http://localhost:8432/pondapi/execute/abc-123/result \
  -H "Authorization: Bearer $TOKEN"
# {"status": "complete", "rows": [{"answer": 42}], "elapsed_ms": 12}
```

## For AI Agents

PondDB gives AI agents structured data access. Instead of hallucinating numbers, agents query real data via HTTP.

### Why agents need PondDB

| Without PondDB | With PondDB |
|----------------|-------------|
| Agent guesses numbers from training data | Agent queries live data with SQL |
| "Revenue was approximately $2M" | `SELECT SUM(revenue) FROM sales` → $2,847,103 |
| No access control | Scoped API keys per agent |
| No audit trail | Full query history with timestamps |

### How agents connect

Any agent that can make HTTP calls can use PondDB:

```bash
# Agent submits a query
curl -X POST http://localhost:8432/pondapi/execute \
  -H "Authorization: Bearer pk_agent_key" \
  -d '{"sql": "SELECT COUNT(*) as users FROM signups WHERE date > CURRENT_DATE - 7"}'

# Agent polls for result
curl http://localhost:8432/pondapi/execute/{id}/result \
  -H "Authorization: Bearer pk_agent_key"
# {"status": "complete", "rows": [{"users": 342}]}
```

PondAPI is async by design — submit SQL, get an execution ID, poll for results. Perfect for agents that need to interleave reasoning with data retrieval.

### Agent-friendly features

- **Async HTTP API** — non-blocking query execution via PondAPI
- **Scoped API keys** — give each agent its own key with limited permissions
- **SQL sandbox** — 15 blocked patterns prevent agents from accessing files or changing config
- **Session auto-management** — sessions suspend when idle, resume transparently
- **Query history** — full audit trail of every query an agent runs

## Features

🔍 **Browser SQL Editor** — CodeMirror 6 with syntax highlighting, schema browser, and auto-complete

📊 **PondAPI** — Async HTTP query API. POST SQL, poll for results. Build apps on top of PondDB.

🤖 **AI Agent Ready** — HTTP API + SQL sandbox = safe, structured data access for any AI agent

👥 **Workgroups** — Isolated compute environments per team. Separate quotas, data, and access.

🔐 **OAuth Login** — Google + GitHub sign-in. Invite-gated registration.

📁 **Dataset Upload** — Drag and drop CSV/Parquet. Queryable instantly.

🔗 **Share Queries** — Save queries, generate public share links at `/q/{slug}`.

🏠 **Self-Hosted** — Docker Compose. Your hardware, your data, your rules.

⚡ **Session Lifecycle** — Auto-suspend idle sessions, transparent resume, <500ms cold start.

🛡️ **SQL Sandbox** — 15 blocked patterns prevent file access, config changes, and extension loading.

## Use cases

**🎓 CS class** — Professor sets up PondDB, sends invite links to students. Everyone has a SQL editor. No installs, no cloud accounts.

**🏗️ Hackathon** — One laptop runs PondDB. Teammates connect via browser. Upload the dataset, share queries in real-time.

**🚀 Startup analytics** — 3 engineers need shared SQL access. PondDB on a $5/month VPS. Workgroup per team.

**🏠 Homelab** — Self-host alongside Nextcloud and Gitea. Your data never leaves your network.

**🤖 AI agent backend** — Give your LLM agent a PondDB API key. It queries real data instead of hallucinating numbers.

## Architecture

```
Browser / SDK / curl / AI Agent
              │
              ▼
┌─────────────────────────────────────────────────┐
│              FastAPI + Uvicorn                   │
│  Routers: query, auth, session, pondapi,        │
│  datasets, queries, share, schema, invites,     │
│  namespaces, oauth, admin, htmx, health         │
└──────┬───────────────────────┬──────────────────┘
       │                       │
       ▼                       ▼
┌──────────────────┐   ┌─────────────────────────┐
│  Session Manager │   │    Auth + Sandbox        │
│  (DuckDB pool,   │   │  (JWT, OAuth, 15        │
│   idle watchdog) │   │   blocked patterns)      │
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

- **COLD → ACTIVE**: `POST /session` spins up a DuckDB connection (< 500 ms)
- **ACTIVE → SUSPENDED**: Watchdog fires after `POND_IDLE_TIMEOUT` seconds (default 300)
- **SUSPENDED → ACTIVE**: Next query triggers transparent resume — datasets re-registered
- **ANY → DESTROYED**: `DELETE /session/{id}`, max age exceeded, or reaper cleanup

## API Reference

### Core endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | — | Server status, version, session count |
| `POST` | `/session` | — | Create a DuckDB session |
| `DELETE` | `/session/{id}` | — | Destroy a session |
| `GET` | `/sessions` | — | List all sessions |
| `POST` | `/query` | JWT | Execute SQL synchronously |
| `GET` | `/schema` | JWT | Table and column introspection |
| `GET` | `/history` | JWT | Query execution history |
| `GET` | `/metrics` | — | Prometheus-compatible metrics |

### PondAPI (async execution)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/pondapi/execute` | JWT | Submit SQL for async execution |
| `GET` | `/pondapi/execute/{id}/result` | JWT | Poll execution result |

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/token` | — | Exchange API key for JWT |
| `POST` | `/auth/refresh` | — | Refresh an access token |
| `POST` | `/auth/revoke` | JWT | Revoke a token |

### Data

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/datasets` | API Key | Upload CSV or Parquet file |
| `GET` | `/datasets` | API Key | List uploaded datasets |
| `DELETE` | `/datasets/{name}` | API Key | Delete a dataset |
| `POST` | `/catalog/mount` | JWT | Mount a local file as a DuckDB table |
| `POST` | `/queries` | JWT | Save a named query |
| `GET` | `/queries` | JWT | List saved queries |
| `GET` | `/q/{slug}` | — | Execute a shared query (public link) |
| `GET` | `/editor` | — | Browser-based SQL editor |

### Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/namespaces` | Admin JWT | Create a namespace |
| `POST` | `/workgroups` | Admin JWT | Create a workgroup with compute quota |
| `POST` | `/invites` | Admin JWT | Generate an invite token |
| `POST` | `/invites/{token}/accept` | — | Accept an invite |

Full reference: [`docs/api.md`](docs/api.md)

## Configuration

Copy [`.env.example`](.env.example) and set at minimum:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POND_API_KEY` | ✅ | — | Master API key for authentication |
| `POND_JWT_SECRET` | ✅ | — | HS256 signing secret (≥ 16 chars) |
| `POND_WEBSITE_SESSION_SECRET` | ✅ | — | Cookie signing secret for the dashboard |
| `POND_HOST` | | `0.0.0.0` | Server bind address |
| `POND_PORT` | | `8432` | Server listen port |
| `POND_IDLE_TIMEOUT` | | `300` | Seconds before an idle session is suspended |
| `POND_MAX_SESSION_AGE` | | `86400` | Max session lifetime in seconds |
| `POND_DATA_ROOT` | | `./data` | Root directory for uploaded datasets |
| `POND_MAX_RESULT_MB` | | `100` | Maximum query result size in MB |
| `POND_SESSION_MEMORY_LIMIT` | | `2GB` | DuckDB memory limit per session |
| `POND_LOG_LEVEL` | | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `POND_SQLITE_PATH` | | `./ponddb.db` | Path to the SQLite metadata database |

Run `pond check` to validate your environment. See [Configuration docs](docs/configuration.md) for all variables including OAuth, SMTP, and rate limiting.

## Development

```bash
git clone https://github.com/pond-db/pond-db.git
cd pond-db
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                              # 2,580 tests
pytest tests/test_browser.py -v     # Playwright browser tests
ruff check src/ tests/              # Lint
```

## Secret Management

PondDB uses `detect-secrets` for pre-commit secret scanning. A `.secrets.baseline` tracks known non-secrets.

**Rotating the JWT secret** (zero-downtime):

```bash
scripts/rotate_jwt_secret.sh
```

The script generates a new secret, promotes the current value to `POND_JWT_SECRET_V1`, and writes `POND_JWT_SECRET_V2` for the new key. Both are accepted during the rotation window.

```
POND_JWT_SECRET_V1=<old-secret>   # accepted for existing tokens
POND_JWT_SECRET_V2=<new-secret>   # used for new tokens
```

See [docs/security.md](docs/security.md) for the full rotation runbook.

## Documentation

- [Architecture](./ARCHITECTURE.md) — system design and components
- [API Reference](./docs/api.md) — every endpoint
- [Configuration](./docs/configuration.md) — all environment variables
- [Security](./docs/security.md) — auth model, threat model, hardening
- [Contributing](./CONTRIBUTING.md) — how to help
- [Changelog](./CHANGELOG.md) — version history

## License

[BSL 1.1](LICENSE) — free to use, self-host, and modify. Converts to **Apache 2.0** on 2029-03-16.

---

**Built with [DuckDB](https://duckdb.org)** · [pond-db.github.io](https://pond-db.github.io) · Made by [DatabaseCompany](https://github.com/DatabaseCompany)
