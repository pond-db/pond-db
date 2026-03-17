# Changelog

All notable changes to PondDB are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-03-17

### Added

- **Core Engine**: DuckDB-backed query engine with session lifecycle (COLD→ACTIVE→SUSPENDED→DESTROYED)
- **PondAPI**: Async SQL execution with polling, ThreadPoolExecutor (8 workers), configurable rate limiting per-tenant
- **SQL Sandbox**: 15 blocked patterns (COPY, LOAD, INSTALL, ATTACH, SET, PRAGMA, read_csv, read_parquet, read_json, read_text, read_blob, glob, EXPORT/IMPORT DATABASE, CREATE SECRET) — DuckDB `enable_external_access=False`, `lock_configuration=true`
- **Authentication**: JWT tokens (access + refresh, HS256) with API key exchange, configurable expiry, token revocation blocklist
- **Multi-Tenant Isolation**: Query store, query history, and executions scoped per tenant_id (namespace + workgroup hierarchy)
- **Session Quota Enforcement**: `max_concurrent_sessions` enforced at session creation; smart resume of suspended sessions
- **OAuth Integration**: Google and GitHub OAuth2 with HMAC state tokens for CSRF protection
- **Invite System**: Token-based invites with SMTP email delivery, accept/revoke lifecycle
- **Query Store**: Named queries with auto-generated slugs, public/private visibility, pagination
- **Share Links**: Public query execution via `/q/{slug}` with token-bucket rate limiting (10 req/min per IP)
- **Query History**: Execution log with status filtering, date range queries, 5-minute TTL caching
- **Dataset Manager**: CSV/Parquet upload via multipart form, auto-registration as DuckDB views on session resume
- **Schema Browser**: Session-scoped table introspection with column metadata, click-to-insert
- **SQL Editor**: CodeMirror 6 (esm.sh CDN), HTMX-powered execution, save/share/run action bar
- **SaaS Dashboard**: Custom CSS, sidebar navigation, stat cards, status badges, breadcrumbs
- **Admin Console**: Invite management, namespace/workgroup CRUD, quota editing, usage monitoring
- **Rate Limiting**: Token-bucket per-tenant rate limiting on PondAPI; IP-based rate limiting on share links
- **Brute Force Protection**: Account lockout after 5 failed login attempts, auto-unlock after window
- **Security Headers**: HSTS, X-Frame-Options, X-Content-Type-Options, CSP, Referrer-Policy
- **CORS**: Allowlist-based CORS with configurable origins
- **Audit Logging**: ASGI middleware for request/response logging; SQL sandbox block events
- **Refresh Fingerprinting**: Device fingerprint bound to refresh tokens to prevent token reuse
- **Health Endpoints**: `/health` status, `/health/security` with P0/P1 control checks
- **Metrics**: `/metrics` endpoint in Prometheus text format
- **Python SDK**: `PondClient` (formerly DuckCloudClient) with auto-refresh, retry, session management
- **CLI**: `pond serve`, `pond version`, `pond check` commands via Click
- **Docker Support**: Multi-stage Dockerfile (non-root user, health check), docker-compose.yml with nginx reverse proxy
- **GitHub Actions CI**: Lint + test + build on push/PR, browser test exclusion (require live server)
- **Release Workflow**: Docker image build and push to ghcr.io on version tag
- **Demo Scripts**: Data generator (sales/users/events CSVs), end-to-end demo, admin demo
- **2,550+ tests**, 92% code coverage

### Security

- SQL sandbox prevents file access, config changes, and extension loading
- JWT secret rotation with zero-downtime via `POND_JWT_SECRET_V1` rollover
- `detect-secrets` pre-commit hook with baseline
- No secrets committed to version control

### Changed

- License changed from MIT to Business Source License 1.1 (converts to Apache 2.0 on 2029-03-16)
- Contact email is now configurable via `POND_CONTACT_EMAIL` environment variable

## [0.1.0] - 2026-03-16

### Added

- Initial OSS release preparation
- Core session manager with idle auto-suspend and transparent resume
- FastAPI skeleton with /session, /query, /health endpoints
- JWT auth + namespace isolation + catalog mount
- Python SDK + CLI + /metrics
- Docker image + GitHub Actions CI
