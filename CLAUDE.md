# PondDB — Lightweight Self-Hosted DuckDB Compute Platform

## What this is
PondDB wraps DuckDB with a serverless operational model: on-demand session spin-up, auto-suspend on idle, per-session resource limits, and a clean query API. Ships as both a standalone Docker server and an embeddable Python library (`pip install ponddb`). MotherDuck ergonomics, zero cloud dependency.

## Architecture (from HLD)
- **Framework:** FastAPI + Uvicorn on port 8432
- **Engine:** DuckDB (one connection per session, file-backed for isolation)
- **Metadata:** SQLite via aiosqlite (session state, catalog mounts, compute log)
- **Auth:** JWT HS256 via python-jose; tokens scoped to namespaces
- **Metrics:** Prometheus-compatible `/metrics` endpoint
- **CLI:** `pond` entrypoint via click

## Session lifecycle
```
COLD → ACTIVE → SUSPENDED → DESTROYED
```
- COLD→ACTIVE: POST /session creates DuckDB connection (<500ms target)
- ACTIVE→SUSPENDED: idle watchdog fires after POND_IDLE_TIMEOUT (default 300s)
- SUSPENDED→ACTIVE: next query triggers transparent resume (<300ms target)
- ANY→DESTROYED: DELETE /session/{id} or max age exceeded

## Key design decisions
- One DuckDB connection per session (file-backed) for true isolation
- Suspend = destroy connection + persist catalog mounts to SQLite; resume = replay mounts
- In-memory tables are LOST on suspend — only catalog-mounted files survive
- Compute accounting via time.perf_counter + resource.getrusage (approximation)
- Library API designed first; server is thin HTTP wrapper over library

## API endpoints
- GET /health — {"status": "ok", "version": str, "sessions": int}
- POST /session — create session
- DELETE /session/{id} — destroy session
- POST /query — execute SQL (body: {session_id, sql, format: json|arrow})
- POST /catalog/mount — mount local file (body: {session_id, path, alias})
- GET /sessions — list sessions for authenticated namespace
- GET /metrics — Prometheus text format

## Project structure
```
src/ponddb/       — library + server code
tests/            — pytest tests
docs/             — documentation and runbooks
```

## Dependencies (core)
fastapi, uvicorn, duckdb, aiosqlite, python-jose, click, httpx, python-json-logger

## Design tenets
1. Simplicity over features — ship working v1 before adding bells
2. Self-hosted first, cloud never
3. Zero-config default, full-config possible
4. Failure is loud, not silent — structured JSON errors
5. Embeddability is first-class — library API before server

## Milestones
- M1: Session Manager + FastAPI skeleton (/session, /query, /health + tests)
- M2: Serverless lifecycle (idle auto-suspend + resume + SQLite + compute tracker)
- M3: JWT auth + namespace isolation + catalog mount + path validation
- M4: Python SDK + CLI + /metrics
- M5: Docker image + CI + README quickstart + OSS publish

## Rules for Claude Code
- NEVER generate all code at once. One file at a time.
- Explain every design decision before implementing.
- Keep files under 200 lines. Split if larger.
- Always include error handling for external calls (DuckDB, SQLite, filesystem).
- Use type hints on all function signatures.
- Test files go in tests/ directory.

## Environment variables
- POND_HOST (default: 0.0.0.0), POND_PORT (default: 8432)
- POND_JWT_SECRET (required for auth)
- POND_IDLE_TIMEOUT (default: 300), POND_MAX_SESSION_AGE (default: 86400)
- POND_DATA_ROOT (default: ./data), POND_MAX_RESULT_MB (default: 100)
- POND_SESSION_MEMORY_LIMIT (default: 2GB)
- POND_LOG_LEVEL (default: INFO), POND_SQLITE_PATH (default: ./ponddb.db)

## Secrets
- All secrets in .env (never commit)
- POND_JWT_SECRET is the critical secret
