# PondDB Setup Guide

## Prerequisites

- Python 3.11+ (for manual install) OR Docker 24+ (for container install)
- A Linux or macOS host (Windows via WSL2)
- `git` to clone the repository

---

## Option A: Docker (recommended)

### 1. Clone the repository

```bash
git clone https://github.com/your-org/db-engine.git
cd db-engine
```

### 2. Create your environment file

```bash
cp .env.example .env
```

Edit `.env` and set the three required secrets:

```
POND_API_KEY=<strong-random-key>
POND_JWT_SECRET=<at-least-16-char-random-string>
POND_WEBSITE_SESSION_SECRET=<another-random-string>
```

Generate random values with:

```bash
openssl rand -hex 32
```

### 3. Build and start

```bash
docker compose up --build -d
```

### 4. Verify

```bash
curl http://localhost:8432/health
# {"status":"ok","version":"...","sessions":0}
```

---

## Option B: Manual install (pip)

### 1. Clone and create a virtualenv

```bash
git clone https://github.com/your-org/db-engine.git
cd db-engine
python -m venv .venv
source .venv/bin/activate
```

### 2. Install the package

```bash
pip install -e ".[dev]"
```

Or install only runtime dependencies:

```bash
pip install -e .
```

### 3. Create your environment file

```bash
cp .env.example .env
```

Fill in the required values (see Configuration section).

### 4. Load environment and start the server

```bash
set -a && source .env && set +a
pond serve
```

Or with `uvicorn` directly:

```bash
uvicorn ponddb.app:app --host 0.0.0.0 --port 8432
```

### 5. Verify the install

```bash
# Health check
curl http://localhost:8432/health

# Security controls check
curl http://localhost:8432/health/security

# Issue a token
curl -s -X POST http://localhost:8432/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-api-key"}' | python -m json.tool

# Create a session and run a query
TOKEN="<access_token from above>"
SESSION=$(curl -s -X POST http://localhost:8432/session | python -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

curl -s -X POST http://localhost:8432/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION\", \"sql\": \"SELECT 42 AS answer\"}"
```

---

## CLI commands

After install, the `pond` CLI is available:

| Command | Description |
|---|---|
| `pond serve` | Start the server |
| `pond check` | Validate configuration |
| `pond version` | Print version |

---

## Data directories

| Path | Purpose |
|---|---|
| `POND_SQLITE_PATH` (default `./ponddb.db`) | SQLite metadata store |
| `POND_DATA_ROOT` (default `./data`) | Uploaded dataset files |
| DuckDB session files | Created under `POND_DATA_ROOT/sessions/` |

Both paths should be on a persistent volume when running in Docker.

---

## Upgrading

```bash
git pull
pip install -e .           # or rebuild Docker image
```

SQLite schema migrations run automatically on startup (idempotent `ALTER TABLE` statements).
