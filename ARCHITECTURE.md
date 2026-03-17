# PondDB Architecture

## Overview

PondDB is a self-hosted DuckDB compute platform. It wraps DuckDB with a serverless operational model: on-demand session spin-up, idle auto-suspend, per-session resource limits, and a clean query API. It ships as both a standalone server (Docker or `uvicorn`) and an embeddable Python library (`pip install ponddb`).

---

## System Components

```
                          ┌─────────────────────────────────┐
                          │           Clients                │
                          │  Browser / SDK / curl / HTMX    │
                          └────────────┬────────────────────┘
                                       │ HTTP (port 8432)
                          ┌────────────▼────────────────────┐
                          │         FastAPI + Uvicorn        │
                          │   SecurityHeadersMiddleware       │
                          │   BruteForceMiddleware (opt)      │
                          │   RateLimitMiddleware (opt/Redis) │
                          │   AllowlistCORSMiddleware (opt)   │
                          │   AuditLogMiddleware              │
                          └──┬─────────────┬────────────────┘
                             │             │
              ┌──────────────▼──┐    ┌─────▼──────────────────┐
              │  SessionManager  │    │     MetadataStore        │
              │  (in-memory)     │    │  (SQLite via sqlite3)    │
              │                  │    │                          │
              │  DuckDB sessions │    │  sessions                │
              │  (file-backed)   │    │  catalog_mounts          │
              └──────────────────┘    │  compute_log             │
                                      │  queries                 │
                                      │  query_history           │
                                      │  invite_tokens           │
                                      └──────────────────────────┘
```

### Component Roles

| Component | Responsibility |
|---|---|
| `FastAPI` | HTTP routing, request validation (Pydantic), OpenAPI docs |
| `SessionManager` | Session lifecycle, DuckDB connection pool, idle watchdog |
| `MetadataStore` | SQLite persistence for sessions, mounts, history, invites |
| `UserStore` | User accounts, OAuth profiles, password hashes |
| `DatasetManager` | File upload storage under `POND_DATA_ROOT` |
| `ResultCache` | In-process LRU read-query cache (5-minute TTL, keyed by tenant) |
| `SqlSandbox` | Pre-execution regex block-list enforcement |
| `JwtAuth` | Token issue/verify/revoke, session cookie signing |

---

## Database Schema

All persistence uses a single SQLite file (`POND_SQLITE_PATH`). Tables:

| Table | Purpose | Key columns |
|---|---|---|
| `sessions` | Active/suspended session state | `session_id`, `namespace`, `state`, `last_active` |
| `catalog_mounts` | File mounts to replay on session resume | `session_id`, `path`, `alias`, `mount_type` |
| `compute_log` | Per-query wall time and memory delta | `session_id`, `query_hash`, `wall_ms`, `mem_delta_kb` |
| `queries` | Saved named queries (shared/private) | `slug`, `sql`, `created_by`, `tenant_id`, `visibility` |
| `query_history` | Execution audit trail | `namespace`, `tenant_id`, `sql`, `duration_ms`, `status` |
| `invite_tokens` | Email-based workspace invitations | `token`, `email`, `tenant_id`, `role`, `status` |

User accounts and OAuth profiles live in a separate logical store (`UserStore`) backed by the same SQLite file.

---

## Session Lifecycle

```
COLD → ACTIVE → SUSPENDED → DESTROYED
```

- **COLD → ACTIVE**: `POST /session` allocates a UUID, opens a DuckDB file connection, registers dataset views.
- **ACTIVE → SUSPENDED**: Idle watchdog fires after `POND_IDLE_TIMEOUT` seconds. Connection is closed; catalog mounts are written to SQLite.
- **SUSPENDED → ACTIVE**: Next query triggers transparent resume — mounts are replayed from SQLite.
- **ANY → DESTROYED**: `DELETE /session/{id}` or max-age exceeded. In-memory tables are lost on suspend.

Sessions survive container restarts: on startup, `SessionManager.load_from_store()` rehydrates state from SQLite.

---

## API Design

### Auth Flow

```
Client                         Server
  │──POST /auth/token ─────────▶│  (api_key in body)
  │◀── {access_token, refresh} ─│  HS256 JWTs
  │
  │──POST /query ───────────────▶│  Authorization: Bearer <token>
  │                              │  OR X-API-Key header
  │                              │  OR pond_session cookie
```

Three credential types are accepted by `require_auth`:
1. `Authorization: Bearer <JWT>` — standard API access
2. `X-API-Key: <key>` — direct API key (maps to `tenant_id=default`)
3. `pond_session` cookie — HMAC-signed dashboard session

### Key REST Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness check |
| `GET` | `/health/security` | None | Security controls status |
| `GET` | `/metrics` | None | Prometheus text format |
| `POST` | `/auth/token` | API key | Issue JWT pair |
| `POST` | `/auth/refresh` | Refresh token | New access token |
| `POST` | `/auth/revoke` | None | Blocklist a token by JTI |
| `POST` | `/session` | None | Create session |
| `DELETE` | `/session/{id}` | None | Destroy session |
| `GET` | `/sessions` | None | List sessions |
| `POST` | `/query` | Bearer/Key/Cookie | Execute SQL |
| `GET` | `/schema` | Bearer/Key/Cookie | Session schema introspection |
| `GET` | `/history` | Bearer/Key/Cookie | Query execution history |
| `GET` | `/metrics` | None | Prometheus metrics |

---

## Multi-Tenancy Model

```
Namespace (JWT sub / API key default)
  └── Workgroup (optional quota container)
        └── Session (one DuckDB connection)
```

- **Namespaces** isolate query history and session ownership.
- **Workgroups** enforce concurrent session quotas (`max_sessions`).
- **Tenant ID** is embedded in the JWT and used as a cache key prefix, preventing cross-tenant cache leakage.

---

## Security Model

| Control | Mechanism |
|---|---|
| SQL sandbox | 15 blocked regex patterns (COPY, LOAD, ATTACH, read_csv, etc.) |
| JWT auth | HS256, configurable expiry, per-token JTI, blocklist via Redis |
| Refresh tokens | 30-day TTL, optional IP+UA fingerprint binding |
| Brute force | Per-IP failure counter; lockout after 5 failures (configurable) |
| Rate limiting | Redis sliding window per IP or API key |
| CORS | Explicit allowlist — no wildcard origins |
| Security headers | CSP, HSTS, X-Frame-Options, X-Content-Type-Options on every response |
| Audit logging | Failed auth, token revoke/refresh, brute-force lockout events |

---

## Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| HTTP server | FastAPI + Uvicorn | Async, auto OpenAPI, Pydantic validation |
| Query engine | DuckDB | Columnar, embeddable, no external process |
| Metadata store | SQLite (sqlite3) | Zero-dependency, single file, WAL mode |
| Auth | python-jose HS256 | Lightweight, no key management infrastructure |
| Frontend | HTMX + CodeMirror 6 | Progressive enhancement, no build step |
| Metrics | Prometheus text format | Standard scrape target, no extra dependency |
| CLI | click | Simple, composable commands |
