<p align="center">
  <img src="static/ponddb-logo-wordmark.svg" alt="PondDB" height="50">
</p>

<p align="center">
  <strong>Share DuckDB with your team — or let your AI agents query it.</strong>
</p>

<p align="center">
  <a href="https://github.com/pond-db/pond-db/actions/workflows/ci.yml"><img src="https://github.com/pond-db/pond-db/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/tests-2%2C580%20passing-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-92%25-brightgreen" alt="Coverage">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-BSL%201.1-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
</p>

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

PondDB gives your agent team a shared, queryable memory database.

### The problem

Multi-agent systems stitch together Redis + Postgres + Pinecone for memory. When something goes wrong, you can't answer: **"What did the agent know when it made that decision?"**

### The solution

PondDB stores agent memories as structured data in one database. Every operation is logged. Debug your agents with SQL, not guesswork.

**Store a memory:**

```bash
curl -X POST http://localhost:8432/memories \
  -H "X-API-Key: $API_KEY" \
  -d '{"agent_id": "researcher", "memory_type": "semantic",
       "content": {"fact": "Q1 revenue was $2.1M"},
       "access_scope": "workgroup", "importance": 0.9}'
```

**Search memories:**

```bash
curl "http://localhost:8432/memories/search?memory_type=semantic&min_importance=0.7" \
  -H "X-API-Key: $API_KEY"
```

**The query no other memory system can run:**

```sql
-- "What did the agent know right before it failed?"
SELECT mal.agent_id, am.content, am.utility
FROM memory_access_log mal
JOIN agent_memories am ON am.id IN (SELECT json_each.value FROM json_each(mal.memory_ids))
WHERE mal.status = 'error'
ORDER BY mal.created_at DESC;
```

### Memory types

| Type | Purpose | Lifetime |
|------|---------|----------|
| `working` | Current task context | Auto-expires (configurable TTL) |
| `episodic` | Interaction history | Permanent |
| `semantic` | Extracted facts | Permanent |
| `procedural` | Learned patterns | Permanent |
| `shared` | Cross-agent team state | Permanent |

### Multi-agent isolation

Agents are isolated by workgroup. Cross-workgroup sharing requires explicit grants:

```bash
# Grant research-team read access to analysis-team's semantic memories
curl -X POST http://localhost:8432/memory-grants \
  -H "X-API-Key: $API_KEY" \
  -d '{"grantor_workgroup_id": "analysis-team-id",
       "grantee_workgroup_id": "research-team-id",
       "memory_type_filter": "semantic",
       "permission": "read"}'
```

### Use with Claude Code (MCP)

```bash
pip install mcp-server-ponddb
```

Add to your MCP config:

```json
{
  "mcpServers": {
    "ponddb": {
      "command": "python", "args": ["-m", "mcp_server_ponddb"],
      "env": {"PONDDB_URL": "http://localhost:8432", "PONDDB_API_KEY": "your-key"}
    }
  }
}
```

Then: *"Remember that our Q1 revenue was $2.1M"* → stored in PondDB.

### Benchmarks

| Metric | Result |
|--------|--------|
| Memory write (single) | < 5ms p50 |
| Memory write (10 concurrent) | < 15ms p50 |
| Memory search (10K memories) | < 10ms p50 |
| Isolation (10K cross-WG queries) | **0 leaks** |
| Grant check overhead | < 2ms |
| Access log overhead | < 1ms |

[Full benchmark results](./benchmarks/RESULTS.md)

### Works with every framework

| Framework | Integration | Status |
|-----------|------------|--------|
| LangGraph | PondDB tools for agents | [Example](./examples/langgraph-data-analyst/) |
| Claude Code | MCP server | [Setup guide](./examples/claude-code-mcp/) |
| CrewAI | PondDB as crew shared workspace | Coming |
| Any HTTP client | PondAPI + Memory API | Works now |

## Features

🔍 **Browser SQL Editor** — CodeMirror 6 with syntax highlighting, schema browser, and auto-complete

📊 **PondAPI** — Async HTTP query API. POST SQL, poll for results. Build apps on top of PondDB.

🤖 **Agent-Ready** — Give AI agent teams a shared SQL database. LangGraph, CrewAI, Claude Code (MCP) compatible.

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

**🤖 AI agent backend** — Your agent team needs shared state? PondDB workgroup = agent team workspace. Agents write results as SQL tables, query each other's outputs. No hallucinated numbers.

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

### Agent Memory

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/memories` | JWT | Create a memory (5 types, 3 scopes) |
| `GET` | `/memories/search` | JWT | Search with 9 filters + grant-aware |
| `GET` | `/memories/{id}` | JWT | Get single memory with access control |
| `PUT` | `/memories/{id}` | JWT | Update content or importance |
| `DELETE` | `/memories/{id}` | JWT | Soft delete (audit trail preserved) |
| `POST` | `/memories/{id}/feedback` | JWT | Update utility score (-1.0 to 1.0) |
| `POST` | `/memory-grants` | Admin JWT | Create cross-workgroup grant |
| `DELETE` | `/memory-grants/{id}` | Admin JWT | Revoke a grant (immediate) |
| `GET` | `/health/cleanup` | — | Memory cleanup task status |

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

**Built on [DuckDB](https://duckdb.org)** · Created by [Tianlu](https://github.com/houtianlu)
