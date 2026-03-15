# PondDB

[![CI](https://github.com/DatabaseCompany/db-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/DatabaseCompany/db-engine/actions/workflows/ci.yml)

PondDB is a lightweight, self-hosted compute platform built on DuckDB. It wraps DuckDB with
a serverless operational model: on-demand session spin-up, auto-suspend on idle,
per-session resource limits, and a clean REST query API. Ships as both a standalone Docker
server and an embeddable Python library (`pip install ponddb`). MotherDuck ergonomics, zero
cloud dependency.

## Quickstart

### Docker (recommended)

Set your JWT secret and start the stack:

```bash
export POND_JWT_SECRET=change-me-in-production
docker compose up
```

Verify the server is running:

```bash
curl http://localhost:8432/health
# {"status": "ok", "version": "0.1.0", "sessions": 0}
```

### pip install

```bash
pip install ponddb
uvicorn ponddb.app:app --host 0.0.0.0 --port 8432
```

Then verify:

```bash
curl http://localhost:8432/health
```

## Architecture

```
┌─────────────────────────────────────────────┐
│                  Clients                    │
│  (HTTP / Python SDK / CLI)                  │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│            FastAPI + Uvicorn                │
│  /session  /query  /auth  /metrics  /editor │
└──────────┬──────────────────────────────────┘
           │                     │
           ▼                     ▼
┌──────────────────┐   ┌─────────────────────┐
│  Session Manager │   │    JWT Auth Layer   │
│  (idle watchdog) │   │   (python-jose)     │
└────────┬─────────┘   └─────────────────────┘
         │
         ▼
┌──────────────────┐   ┌─────────────────────┐
│   DuckDB Engine  │   │   SQLite Metadata   │
│ (one conn/sess)  │   │  (aiosqlite)        │
└──────────────────┘   └─────────────────────┘
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

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Server health check — version and active session count |
| POST | `/session` | Yes | Create a new DuckDB session |
| DELETE | `/session/{id}` | Yes | Destroy a session immediately |
| GET | `/sessions` | Yes | List all sessions for the authenticated namespace |
| POST | `/query` | Yes | Execute SQL (body: `{session_id, sql, format}`) |
| POST | `/catalog/mount` | Yes | Mount a local file into a session |
| GET | `/metrics` | No | Prometheus-compatible metrics endpoint |
| POST | `/auth/token` | No | Obtain a JWT (body: `{namespace, secret}`) |
| POST | `/auth/refresh` | Yes | Refresh an expiring JWT |
| GET | `/history` | Yes | Query execution history for a session |
| GET | `/schema` | Yes | Table introspection — list tables and columns |
| GET | `/editor` | No | Web-based SQL editor (HTML) |
| GET | `/datasets` | Yes | List uploaded datasets |
| POST | `/datasets` | Yes | Upload a CSV or Parquet dataset |
| DELETE | `/datasets/{name}` | Yes | Delete an uploaded dataset |

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `POND_HOST` | `0.0.0.0` | Server bind host |
| `POND_PORT` | `8432` | Server bind port |
| `POND_JWT_SECRET` | *(required)* | JWT HS256 signing secret |
| `POND_IDLE_TIMEOUT` | `300` | Seconds of idle before auto-suspend |
| `POND_MAX_SESSION_AGE` | `86400` | Maximum session lifetime in seconds |
| `POND_DATA_ROOT` | `./data` | Root directory for catalog file mounts |
| `POND_MAX_RESULT_MB` | `100` | Maximum query result size in MB |
| `POND_SESSION_MEMORY_LIMIT` | `2GB` | Per-session DuckDB memory cap |
| `POND_LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `POND_SQLITE_PATH` | `./ponddb.db` | Path to the SQLite metadata store |

## Python SDK

```python
from ponddb import PondDB

# Connect with an API key (or use token=... for JWT)
client = PondDB(base_url="http://localhost:8432", api_key="your-api-key")

# Use as a context manager — session is created and destroyed automatically
with client as session:
    result = session.query("SELECT 42 AS answer")
    print(result)

# Or manage the session lifecycle manually
client.connect()
result = client.query("SELECT current_timestamp AS ts")
client.close()
```

Obtain a JWT token first if your server requires it:

```python
import httpx
from ponddb import PondDB

resp = httpx.post("http://localhost:8432/auth/token",
                  json={"namespace": "myns", "secret": "mysecret"})
token = resp.json()["access_token"]

client = PondDB(base_url="http://localhost:8432", token=token)
with client as session:
    rows = session.query("SELECT 1+1 AS two")
```

## Development

```bash
git clone https://github.com/DatabaseCompany/db-engine.git
cd db-engine

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

# Run the test suite
pytest

# Lint
ruff check src/ tests/
```

## License

MIT
