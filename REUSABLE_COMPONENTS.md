# Reusable Components for PondLake

Modules from `src/ponddb/` that can be carried into PondLake with little or no
modification. Organised by functional group.

---

## Auth Modules

### `jwt_auth.py`
**Path:** `src/ponddb/jwt_auth.py`

**API surface:**
- `create_access_token(tenant_id, scopes, role) -> str` — signs HS256 access JWT
- `create_refresh_token(tenant_id, ip, user_agent) -> str` — signs HS256 refresh JWT with optional HMAC fingerprint
- `verify_access_token(token) -> dict` — decodes and validates; tries all versioned secrets in fallback order
- `verify_refresh_token(token, ip, user_agent) -> dict` — validates refresh token and optional fingerprint
- `validate_secret_strength(secret)` — rejects secrets shorter than 16 chars or in a known-weak list
- `validate_startup_secret()` — call at startup to fail fast on bad config
- `compute_fingerprint(ip, ua, salt, include_ip) -> str` — HMAC-SHA256 device fingerprint
- `require_auth(request) -> dict` — FastAPI dependency; accepts Bearer JWT, `X-API-Key`, or HMAC-signed session cookie
- `require_admin(request) -> dict` — FastAPI dependency; requires `role=admin` claim in JWT

**PondDB-specific coupling to remove:**
- Secret env vars are all `POND_*` prefixed — rename to `LAKE_*` (or a shared prefix).
- `require_auth` hard-imports `ponddb.audit_log` to log failed auth events; extract the log call into an injectable callback or remove the import.
- `_get_api_key()` reads `POND_API_KEY` / `POND_API_KEY_FILE`; rename accordingly.

---

### `token_blocklist.py`
**Path:** `src/ponddb/token_blocklist.py`

**API surface:**
- `add_to_blocklist(jti)` — add a JWT ID to the in-process revocation set
- `is_revoked(jti) -> bool` — lookup; callers are expected to fail open on exceptions
- `remove_from_blocklist(jti)` — test-cleanup helper

**PondDB-specific coupling to remove:**
- None. The module has zero PondDB imports; drop in as-is.
- Note: storage is in-process only (no Redis/DB persistence). For multi-process PondLake deployments, back this with Redis or a shared DB table before going to production.

---

### `user_store.py`
**Path:** `src/ponddb/user_store.py`

**API surface:**
- `UserStore(db_path)` — SQLite-backed store; call `initialize_blocking()` at startup
- User CRUD: `create_user`, `get_user_by_id`, `get_user_by_email`, `get_user_by_provider_id`, `get_user_by_tenant_id`, `update_user`, `upsert_user`
- Org membership: `add_org_member`, `list_org_members`, `remove_org_member`
- Workgroup membership: `add_workgroup_member`, `list_workgroup_members`, `remove_workgroup_member`
- API key lifecycle: `create_api_key`, `list_api_keys`, `revoke_api_key`, `verify_api_key`

**PondDB-specific coupling to remove:**
- No `ponddb` imports — fully self-contained.
- Schema uses `tenant_id` as the namespace concept; rename to whatever PondLake uses if the terminology differs.
- SQLite is synchronous under async wrappers; fine for low-concurrency single-node deployments. Consider `aiosqlite` or Postgres for higher write throughput.

---

## Security Middleware

### `brute_force.py`
**Path:** `src/ponddb/brute_force.py`

**API surface:**
- `BruteForceGuard(lockout_threshold, lockout_ttl_seconds)` — in-memory per-IP failure counter
  - `is_locked(ip) -> bool`, `record_failure(ip)`, `record_success(ip)`, `get_failure_count(ip) -> int`, `check_or_raise(ip)`
- `BruteForceMiddleware(app, guard)` — Starlette middleware; returns 429 for locked IPs before reaching route handlers

**PondDB-specific coupling to remove:**
- `BruteForceMiddleware.dispatch` imports `ponddb.audit_log` to fire a `brute_force_lockout` event; extract into a configurable log hook or remove the call.
- State is in-process; does not survive restarts or scale across replicas.

---

### `rate_limit.py`
**Path:** `src/ponddb/rate_limit.py`

**API surface:**
- `RateLimiter(redis_client, limit, window_seconds)` — Redis sliding-window counter
  - `check(key) -> (allowed: bool, retry_after: int)` — fails open on Redis errors
- `RateLimitMiddleware(app, redis_client, limit, window_seconds)` — Starlette middleware; rate-limits by `X-API-Key` when present, otherwise by IP

**PondDB-specific coupling to remove:**
- None. Fully generic; no `ponddb` imports.

---

### `cors_middleware.py`
**Path:** `src/ponddb/cors_middleware.py`

**API surface:**
- `AllowlistCORSMiddleware(app, allow_origins: list[str])` — never emits a wildcard `*`; echoes allowed origins; rejects disallowed preflight with 400

**PondDB-specific coupling to remove:**
- None. Zero project-specific references; copy directly.

---

### `security_headers.py`
**Path:** `src/ponddb/security_headers.py`

**API surface:**
- `SecurityHeadersMiddleware(app, dev_mode=False)` — injects `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy`, and (in prod) `Strict-Transport-Security` on every response

**PondDB-specific coupling to remove:**
- The `Content-Security-Policy` header allows `https://esm.sh` and `https://unpkg.com` for script sources (PondDB UI CDN deps). Update the CSP to match PondLake's actual asset origins before deploying.

---

### `sql_sandbox.py`
**Path:** `src/ponddb/sql_sandbox.py`

**API surface:**
- `BlockedSqlError(pattern, sql)` — exception carrying the matched pattern name
- `BLOCKED_PATTERNS: list[re.Pattern]` — compiled regex list (COPY, LOAD, INSTALL, ATTACH, read_csv, read_parquet, glob, etc.)
- `check_sql(sql)` — raises `BlockedSqlError` on match, returns `None` if clean

**PondDB-specific coupling to remove:**
- None. Generic DuckDB SQL guard with no project-specific imports.
- The blocked-pattern list is tuned for DuckDB; review which patterns are relevant to PondLake's query engine before adopting.

---

## API Patterns

### `audit_log.py`
**Path:** `src/ponddb/audit_log.py`

**API surface:**
- `SCHEMA_SQL` — DDL string for the `security_audit_log` Postgres table (with indexes and a `REVOKE DELETE` guard)
- `log_event(pool, event_type, *, tenant_id, ip_address, user_agent, detail)` — fire-and-forget async insert; swallows all exceptions
- `AuditLogMiddleware(app, dsn)` — raw ASGI middleware; intercepts `POST /auth/token` and `POST /query`; logs `login_success`, `login_failure`, and `sandbox_block` events

**PondDB-specific coupling to remove:**
- `AuditLogMiddleware.dispatch` is hard-coded to watch `/auth/token` and `/query` paths; parameterise the watched paths for PondLake's route layout.
- `_detect_blocked_pattern` imports `ponddb.sql_sandbox`; acceptable if PondLake also uses `sql_sandbox.py`, otherwise remove.
- Pool is held as a class-level `_pool` attribute (test-patchable but not constructor-injectable); refactor to pass the pool via the constructor for cleaner dependency management.

---

### `metadata_store.py`
**Path:** `src/ponddb/metadata_store.py`

**API surface:**
- `MetadataStore(db_path)` — SQLite store; call `initialize()` (async) or `initialize_blocking()` at startup
- Session state: `save_session`, `load_sessions`, `delete_session`
- Catalog mounts: `save_mount`, `list_mounts`, `delete_mounts`
- Compute accounting: `log_compute_sample`, `get_compute_samples`
- Query history: `log_query_history`, `get_query_history` (filterable by namespace, tenant, status, time range, with limit/offset)
- Invite tokens: schema is created but operations are provided via `QueryStoreMixin` (separate file)

**PondDB-specific coupling to remove:**
- Inherits from `ponddb.query_store.QueryStoreMixin`; bring along `query_store.py` or flatten the mixin into this class.
- `sessions` and `catalog_mounts` tables are tightly coupled to PondDB's session lifecycle model. For PondLake, the query history and compute log tables are the most transferable; the session/mount tables can be dropped or replaced.
- Inline schema migrations (`ALTER TABLE ... ADD COLUMN`) are handled with bare `try/except`; replace with a proper migration tool (Alembic, yoyo) for production use.
