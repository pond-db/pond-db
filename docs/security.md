# PondDB Security Reference

PondDB ships with multiple defence-in-depth controls. All controls can be verified at runtime via `GET /health/security`.

---

## 1. SQL Sandbox

**Source**: `src/ponddb/sql_sandbox.py`

Every SQL string is checked against 15 regex patterns before execution. Any match raises a `BlockedSqlError`, which the query endpoint returns as `HTTP 403`.

### Blocked patterns

| Pattern name | What it blocks |
|---|---|
| `COPY` | `COPY … TO/FROM` filesystem access |
| `LOAD` | Loading extensions from disk |
| `INSTALL` | Installing DuckDB extensions |
| `ATTACH` | Attaching external databases |
| `EXPORT DATABASE` | Full database export |
| `IMPORT DATABASE` | Full database import |
| `CREATE SECRET` | Storing credentials in DuckDB |
| `SET` | Changing DuckDB session settings |
| `PRAGMA` | DuckDB pragma statements |
| `read_csv` | Direct CSV file reads |
| `read_parquet` | Direct Parquet file reads |
| `read_json` | Direct JSON file reads |
| `read_text` | Direct text file reads |
| `read_blob` | Direct blob file reads |
| `glob` | Filesystem glob function |

Patterns are case-insensitive. Most anchor to the start of the statement; file-reader patterns match anywhere in the SQL (including subqueries).

**There is no configuration to disable specific patterns.** The sandbox is hardcoded for safety. To expose data files, use the dataset upload API (`/datasets`) instead of direct file reads.

---

## 2. JWT Authentication

**Source**: `src/ponddb/jwt_auth.py`

Tokens are signed with HMAC-SHA256 (`python-jose`).

### Access tokens
- Default TTL: 1 hour (`POND_JWT_EXPIRY_SECONDS`)
- Claims: `sub`, `tenant_id`, `scopes`, `type=access`, `jti`, `iat`, `exp`
- Optional `role` claim for admin elevation

### Refresh tokens
- Default TTL: 30 days
- Claims include `type=refresh` and a `jti` for revocation
- Optional IP+UA fingerprint (`fp` claim) bound at issue time

### Secret priority order (highest first)

1. `POND_JWT_SECRET_FILE` — file-based (Docker secrets compatible)
2. `POND_JWT_SECRET_V2` — versioned primary
3. `POND_JWT_SECRET_V1` — versioned fallback (verify only)
4. `POND_JWT_SECRET` — base env var

### Secret strength enforcement

The server validates the secret at startup: minimum 16 characters, and rejects common weak values (`password`, `secret`, `changeme`, `letmein`, `qwerty`).

### Zero-downtime secret rotation

1. Put the new secret in `POND_JWT_SECRET_V2`.
2. Move the old secret to `POND_JWT_SECRET_V1`.
3. Restart. New tokens are signed with V2; V1 tokens continue to verify.
4. After all V1 tokens expire, remove `POND_JWT_SECRET_V1`.

### Token revocation

`POST /auth/revoke` adds a token's `jti` to an in-process blocklist. For persistence across restarts, set `POND_REDIS_URL` — this enables Redis-backed revocation (`jwt_revocation_enabled` control). Without Redis, revocation is process-local only.

---

## 3. Brute Force Protection

**Source**: `src/ponddb/brute_force.py`

`BruteForceGuard` tracks failed authentication attempts per client IP.

- **Default threshold**: 5 failures before lockout
- **Lockout response**: `HTTP 429` with `{"detail": "Too many failed attempts…"}`
- **TTL**: Configurable lockout duration; expired lockouts reset automatically
- Lockout events are emitted to the audit log

`BruteForceMiddleware` applies the check before route handlers execute, so locked IPs are rejected before any auth logic runs.

---

## 4. Rate Limiting

**Source**: `src/ponddb/rate_limit.py`

`RateLimiter` implements a Redis sliding window algorithm.

- **Algorithm**: sorted-set per key, pruned on each request
- **Keys**: `key:<api-key>` when an API key is present; `ip:<ip>` otherwise
- **Default**: 100 requests per 60-second window (for the global middleware)
- **PondAPI endpoints**: configurable via `POND_PONDAPI_RATE_LIMIT` / `POND_PONDAPI_RATE_WINDOW`
- **Fail-open**: if Redis is unavailable, the limiter logs a warning and allows the request
- Blocked requests return `HTTP 429` with a `Retry-After` header

Redis is required for rate limiting. Without `POND_REDIS_URL`, the middleware is present but operates in fail-open mode.

---

## 5. CORS

**Source**: `src/ponddb/cors_middleware.py`

`AllowlistCORSMiddleware` enforces an explicit origin allowlist — wildcard (`*`) origins are never emitted.

**Configuration**: Set `POND_CORS_ORIGINS` to a comma-separated list of allowed origins:

```
POND_CORS_ORIGINS=https://app.example.com,https://staging.example.com
```

If `POND_CORS_ORIGINS` is empty, the CORS middleware is not mounted and cross-origin requests receive no CORS headers (effectively denied by browsers).

---

## 6. Security Headers

**Source**: `src/ponddb/security_headers.py`

`SecurityHeadersMiddleware` adds the following headers to every response:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |
| `Content-Security-Policy` | Restricts scripts to `self` + `esm.sh` + `unpkg.com`; no external images or connections |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | Disables geolocation, microphone, camera |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (omitted in dev mode) |

HSTS is omitted when the middleware is constructed with `dev_mode=True`. This is not exposed as a configuration variable; modify the middleware instantiation in `app.py` for local HTTP development.

---

## 7. Audit Logging

**Source**: `src/ponddb/audit_log.py`

`AuditLogMiddleware` records security-relevant events:

| Event type | Trigger |
|---|---|
| `failed_auth` | Any `401` from `require_auth` |
| `token_refresh` | `POST /auth/refresh` (success and failure) |
| `token_revoke` | `POST /auth/revoke` |
| `brute_force_lockout` | IP blocked by `BruteForceMiddleware` |

Audit records include timestamp, event type, `tenant_id`, IP address, user agent, and a detail string.

---

## 8. Security Health Check

`GET /health/security` evaluates all security controls and returns their status:

```bash
curl http://localhost:8432/health/security
```

Returns `HTTP 200` when all P0 controls pass, or `HTTP 503` if any P0 control is failing. Use this endpoint in readiness probes and alerting to detect configuration drift.

P0 controls (any `false` → 503): `jwt_secret_configured`, `sql_sandbox_enabled`, `security_headers_enabled`, `brute_force_protection_enabled`, `rate_limiting_enabled`, `audit_logging_enabled`, `cors_configured`.

P1 controls (informational only): `jwt_revocation_enabled` (requires Redis).
