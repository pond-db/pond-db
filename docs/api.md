# PondDB REST API Reference

Base URL: `http://localhost:8432` (default)

Interactive docs: `GET /docs` (Swagger UI) or `GET /redoc`

---

## Authentication

Three credential types are accepted on protected endpoints:

| Method | Header / Cookie | Notes |
|---|---|---|
| Bearer JWT | `Authorization: Bearer <access_token>` | Obtained from `POST /auth/token` |
| API key | `X-API-Key: <key>` | Equals `POND_API_KEY`; maps to `tenant_id=default` |
| Session cookie | `pond_session` cookie | Set automatically by the dashboard login flow |

Endpoints marked **Auth required** accept any of the three.

---

## Health

### GET /health

No auth required.

**Response 200**
```json
{"status": "ok", "version": "0.1.0", "sessions": 3}
```

### GET /health/security

No auth required. Returns security control status. Returns `503` if any P0 control is disabled.

**Response 200**
```json
{
  "status": "healthy",
  "controls": {
    "jwt_secret_configured": true,
    "sql_sandbox_enabled": true,
    "security_headers_enabled": true,
    "brute_force_protection_enabled": true,
    "rate_limiting_enabled": true,
    "audit_logging_enabled": true,
    "cors_configured": true,
    "jwt_revocation_enabled": false
  },
  "p0_controls": ["audit_logging_enabled", "brute_force_protection_enabled", ...]
}
```

`jwt_revocation_enabled` is a P1 control — `false` does not trigger `503`.

### GET /metrics

No auth required. Prometheus text format.

Exposed metrics:
- `ponddb_sessions_active` — current active sessions (gauge)
- `ponddb_query_duration_seconds` — query wall time histogram
- `ponddb_compute_units_total` — total compute ms consumed (counter)

---

## Auth Tokens

### POST /auth/token

Exchange an API key for a JWT access token and refresh token.

**Request body**
```json
{"api_key": "your-api-key", "tenant_id": "acme"}
```
`tenant_id` is optional; defaults to `"default"`.

**Response 200**
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Errors**: `401` — invalid API key.

### POST /auth/refresh

Issue a new access token from a valid refresh token.

**Request body**
```json
{"refresh_token": "<jwt>"}
```

**Response 200**
```json
{"access_token": "<jwt>", "token_type": "bearer", "expires_in": 3600}
```

**Errors**: `401` — expired, revoked, or fingerprint mismatch.

### POST /auth/revoke

Add a token's `jti` to the revocation blocklist. Works for both access and refresh tokens, including already-expired ones.

**Request body**
```json
{"token": "<jwt>"}
```

**Response 200**
```json
{"detail": "revoked", "jti": "<uuid>"}
```

---

## Sessions

### POST /session

Create a new session. No auth required.

**Request body** (optional)
```json
{"namespace": "default", "workgroup_id": "team-a"}
```

**Response 201**
```json
{"session_id": "<uuid>", "status": "ACTIVE", "workgroup_id": "default"}
```

**Errors**: `404` — workgroup not found; `429` — workgroup session quota exceeded.

### DELETE /session/{session_id}

Destroy a session immediately.

**Response 200**
```json
{"detail": "destroyed"}
```

**Errors**: `404` — session not found.

### GET /sessions

List sessions. Optional filters: `?namespace=X` or `?workgroup_id=Y`.

**Response 200**
```json
[
  {
    "session_id": "<uuid>",
    "status": "ACTIVE",
    "namespace": "default",
    "created_at": "2026-03-17T12:00:00+00:00",
    "last_active": "2026-03-17T12:05:00+00:00",
    "workgroup_id": "default"
  }
]
```

---

## Query Execution

### POST /query

Execute SQL against an existing session. **Auth required.**

SQL is validated against the sandbox block-list before execution. Read queries are cached (5-minute TTL, keyed by tenant + dataset version).

**Request body**
```json
{"session_id": "<uuid>", "sql": "SELECT 1 + 1 AS result", "format": "json"}
```

`format` must be `"json"` (only supported value).
`sql` max length is 50,000 characters.

**Response 200**
```json
{
  "columns": ["result"],
  "rows": [[2]],
  "rowcount": 1,
  "elapsed_ms": 4.2
}
```

Response header `X-Cache: HIT` or `MISS` indicates cache status.

**Errors**:
- `400` — empty SQL, unsupported format, or DuckDB query error
- `403` — SQL matched a blocked pattern, or workgroup isolation violation
- `404` — session not found

### GET /schema

Introspect tables and columns in an active session. **Auth required.**

**Query param**: `session_id=<uuid>`

**Response 200**
```json
[
  {
    "table_name": "sales",
    "columns": [
      {"name": "id", "type": "INTEGER"},
      {"name": "amount", "type": "DOUBLE"}
    ]
  }
]
```

---

## Query History

### GET /history

Return query execution history for the authenticated tenant. **Auth required.**

**Query params** (all optional):
- `status` — `success` or `error`
- `start` — ISO 8601 datetime lower bound
- `end` — ISO 8601 datetime upper bound
- `limit` — integer, default `50`
- `offset` — integer, default `0`

**Response 200** — array of history records:
```json
[
  {
    "namespace": "default",
    "tenant_id": "acme",
    "sql": "SELECT count(*) FROM orders",
    "duration_ms": 12.3,
    "rows_returned": 1,
    "status": "success",
    "error_message": null,
    "executed_at": "2026-03-17T12:01:00+00:00"
  }
]
```

When called from a browser (Accept: text/html), returns the history dashboard page instead.

---

## Error Format

All errors return a JSON body:

```json
{"detail": "Human-readable error message"}
```

Common status codes:
- `400` — bad request (invalid input or SQL error)
- `401` — authentication required or invalid credentials
- `403` — forbidden (blocked SQL pattern, wrong workgroup, insufficient role)
- `404` — resource not found
- `429` — rate limit or session quota exceeded
- `500` — internal server error (check `POND_JWT_SECRET` configuration)
