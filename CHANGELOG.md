# Changelog

All notable changes to PondDB are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-03-16

### Added

- **Core Engine**: DuckDB-backed query engine with session lifecycle (create, suspend, resume, destroy)
- **PondAPI**: Async SQL execution with polling, ThreadPoolExecutor (8 workers), rate limiting (configurable per-tenant)
- **SQL Sandbox**: 15 blocked patterns (COPY, LOAD, INSTALL, ATTACH, SET, PRAGMA, read_csv, read_parquet, read_json, read_text, read_blob, glob, EXPORT/IMPORT DATABASE, CREATE SECRET)
- **DuckDB Hardening**: `enable_external_access=False`, `lock_configuration=true`, per-session memory/thread limits
- **Authentication**: JWT tokens (access + refresh) with API key exchange, configurable expiry
- **Multi-Tenant Isolation**: Query store, query history, and executions scoped per tenant_id
- **OAuth Integration**: Google and GitHub OAuth2 with HMAC state tokens for CSRF protection
- **Invite System**: Token-based invites with SMTP email delivery, accept/revoke lifecycle
- **Query Store**: Named queries with auto-generated slugs, public/private visibility, pagination
- **Share Links**: Public query execution via `/q/{slug}` with token-bucket rate limiting (10 req/min per IP)
- **Query History**: Execution log with status filtering, date range queries, 5-minute TTL caching
- **Dataset Manager**: CSV/Parquet upload via multipart form, auto-registration as DuckDB views
- **Schema Browser**: Session-scoped table introspection with column metadata
- **SQL Editor**: CodeMirror 6 (via esm.sh CDN), HTMX-powered execution, schema sidebar with click-to-insert
- **SaaS Dashboard**: Pico.css v2, sidebar navigation, stat cards, status badges, breadcrumbs
- **Admin Console**: Invite management, namespace/workgroup CRUD, quota editing, usage monitoring
- **Namespace & Workgroup System**: Hierarchical organization with quota enforcement (max sessions, duration, result size)
- **Audit Logging**: ASGI middleware for request/response logging, SQL sandbox block events
- **Health Endpoints**: `/health` status, `/health/security` with P0/P1 control checks
- **Python SDK**: `DuckCloudClient` with auto-refresh, retry, session management
- **CLI**: `pond serve`, `pond version`, `pond check` commands via Click
- **Docker Support**: Multi-stage Dockerfile, docker-compose.yml with health checks
- **GitHub Actions CI**: pytest + coverage on push/PR
- **Demo Scripts**: Data generator (sales/users/events CSVs), end-to-end demo, admin demo
- **2,400+ tests**, 92% code coverage, 49 stress/integration tests
