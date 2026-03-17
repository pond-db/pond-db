# PondDB Configuration Reference

All configuration is driven by environment variables. Copy `.env.example` to `.env` and set values before starting the server. Run `pond check` to validate your configuration.

---

## Required

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_API_KEY` | string | *(none)* | Master API key for all authentication. Used to issue JWTs via `POST /auth/token`. |
| `POND_JWT_SECRET` | string | *(none)* | HMAC-SHA256 signing secret for JWTs. Minimum 16 characters. Never commit this value. |
| `POND_WEBSITE_SESSION_SECRET` | string | *(none)* | HMAC-SHA256 signing secret for dashboard session cookies. |

---

## Server

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_HOST` | string | `0.0.0.0` | Bind address for the Uvicorn server. |
| `POND_PORT` | integer | `8432` | TCP port to listen on. |
| `POND_LOG_LEVEL` | string | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

---

## Storage

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_SQLITE_PATH` | string | `./ponddb.db` | Path to the SQLite metadata store. Use an absolute path in production. |
| `POND_DATA_ROOT` | string | `./data` | Root directory for uploaded dataset files and DuckDB session files. |

---

## Session Lifecycle

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_IDLE_TIMEOUT` | integer (seconds) | `300` | Seconds of inactivity before a session is suspended. |
| `POND_MAX_SESSION_AGE` | integer (seconds) | `86400` | Maximum session age (24 hours). Sessions older than this are destroyed. |
| `POND_SESSION_MEMORY_LIMIT` | string | `2GB` | DuckDB memory limit per session (DuckDB `SET memory_limit` syntax). |
| `POND_SESSION_THREADS` | integer | `4` | DuckDB thread count per session. |
| `POND_WATCHDOG_INTERVAL` | float (seconds) | `30` | How often the idle watchdog polls for expired sessions. |

---

## JWT Configuration

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_JWT_EXPIRY_SECONDS` | integer | `3600` | Access token TTL in seconds (1 hour). |
| `POND_JWT_SECRET_FILE` | string | *(none)* | Path to a file containing the JWT secret. Takes priority over `POND_JWT_SECRET`. Useful for Docker secrets. |
| `POND_JWT_SECRET_V2` | string | *(none)* | Primary versioned JWT secret (for zero-downtime rotation). |
| `POND_JWT_SECRET_V1` | string | *(none)* | Fallback versioned JWT secret (verify-only during rotation). |
| `POND_API_KEY_FILE` | string | *(none)* | Path to a file containing the API key. Takes priority over `POND_API_KEY`. |
| `POND_WEBSITE_SESSION_SECRET_FILE` | string | *(none)* | Path to a file containing the session cookie secret. |
| `POND_FINGERPRINT_SALT` | string | *(none)* | HMAC salt for refresh token IP+UA fingerprint binding. If unset, fingerprinting is skipped. |
| `POND_FINGERPRINT_IP` | boolean | `true` | Include IP in refresh token fingerprint. Set to `false` for NAT/proxy environments. |

---

## Resource Limits

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_MAX_RESULT_MB` | integer | `100` | Maximum query result size in megabytes before the response is rejected. |

---

## PondAPI Rate Limiting

These control the per-tenant rate limit on async PondAPI execution endpoints.

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_PONDAPI_RATE_LIMIT` | integer | `10` | Maximum executions allowed per window per tenant. |
| `POND_PONDAPI_RATE_WINDOW` | integer (seconds) | `60` | Rate limit window size in seconds. |

---

## Redis (optional)

Redis is required to enable global rate limiting and persistent JWT revocation.

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_REDIS_URL` | string | *(none)* | Redis connection URL, e.g. `redis://localhost:6379/0`. If unset, rate limiting degrades gracefully and JWT revocation (`jwt_revocation_enabled`) is disabled. |

---

## OAuth (optional)

Leave all OAuth variables blank to disable OAuth login entirely.

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_GOOGLE_CLIENT_ID` | string | *(none)* | Google OAuth2 client ID. |
| `POND_GOOGLE_CLIENT_SECRET` | string | *(none)* | Google OAuth2 client secret. |
| `POND_GITHUB_CLIENT_ID` | string | *(none)* | GitHub OAuth App client ID. |
| `POND_GITHUB_CLIENT_SECRET` | string | *(none)* | GitHub OAuth App client secret. |
| `POND_OAUTH_SECRET` | string | *(none)* | HMAC secret for OAuth state parameter signing. |
| `POND_OAUTH_REDIRECT_BASE` | string | *(none)* | Public base URL for OAuth callback redirect, e.g. `https://pond.example.com`. |

---

## SMTP — invite emails (optional)

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_SMTP_HOST` | string | *(none)* | SMTP server hostname, e.g. `smtp.gmail.com`. |
| `POND_SMTP_PORT` | integer | *(none)* | SMTP port, typically `587` (STARTTLS) or `465` (SSL). |
| `POND_SMTP_USER` | string | *(none)* | SMTP username / email address. |
| `POND_SMTP_PASSWORD` | string | *(none)* | SMTP password. |
| `POND_SMTP_FROM` | string | *(none)* | From address for invite emails. |
| `POND_BASE_URL` | string | *(none)* | Public base URL embedded in invite email links. |

---

## Landing page (optional)

| Variable | Type | Default | Description |
|---|---|---|---|
| `POND_CONTACT_EMAIL` | string | *(none)* | Email address shown in the public landing page "Request Invite" link. |

---

## Secret rotation

To rotate the JWT secret with zero downtime:

1. Set `POND_JWT_SECRET_V2` to the new secret.
2. Move the old value to `POND_JWT_SECRET_V1`.
3. Restart the server. New tokens are signed with V2; old tokens signed with V1 continue to verify.
4. After all old tokens have expired, remove `POND_JWT_SECRET_V1`.
