"""Query store mixin — named query persistence backed by SQLite."""

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


class DuplicateQueryError(Exception):
    """Raised when a query with the same slug already exists."""


class QueryNotFoundError(KeyError):
    """Raised when a query slug is not found."""


def _make_slug(title: str) -> str:
    """Generate a URL-safe slug (lowercase letters, digits, hyphens) from title."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "query"


class QueryStoreMixin:
    """Mixin that adds named query CRUD to MetadataStore.

    Requires ``self._conn: sqlite3.Connection`` provided by the host class.
    The host class must also create the ``queries`` table in its ``initialize``
    method.
    """

    _conn: sqlite3.Connection  # type: ignore[assignment]  # provided by MetadataStore

    async def save_query(
        self,
        title: str,
        description: str,
        sql: str,
        created_by: str,
        tenant_id: str = "default",
        visibility: str = "private",
    ) -> str:
        """Persist a named query. Returns the generated slug.

        Raises DuplicateQueryError if a query with the same slug already exists.
        """
        slug = _make_slug(title)
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                """
                INSERT INTO queries (slug, title, description, sql, created_by, tenant_id, created_at, visibility)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (slug, title, description, sql, created_by, tenant_id, created_at, visibility),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateQueryError(
                f"Query with slug '{slug}' already exists"
            ) from exc
        return slug

    async def get_query_by_slug(
        self, slug: str, tenant_id: str = "default"
    ) -> dict[str, Any]:
        """Return the query dict or raise QueryNotFoundError.

        Raises PermissionError if the query is private and owned by a different tenant.
        """
        cursor = self._conn.execute(
            "SELECT slug, title, description, sql, created_by, tenant_id, created_at, visibility "
            "FROM queries WHERE slug = ?",
            (slug,),
        )
        row = cursor.fetchone()
        if row is None:
            raise QueryNotFoundError(f"No query found with slug: {slug!r}")
        result = dict(row)
        if result["visibility"] == "private" and result["tenant_id"] != tenant_id:
            raise PermissionError(
                f"Query {slug!r} is private and belongs to a different tenant"
            )
        return result

    async def list_queries(
        self,
        tenant_id: Optional[str] = None,
        include_public: bool = True,
        limit: int = 20,
        offset: int = 0,
        created_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return paginated list of queries.

        When tenant_id is provided:
          - include_public=True (default): own queries + public queries from other tenants.
          - include_public=False: only this tenant's own queries.
        When created_by is provided (legacy): filter by created_by column.
        """
        if tenant_id is not None:
            if include_public:
                cursor = self._conn.execute(
                    "SELECT slug, title, description, sql, created_by, tenant_id, created_at, visibility "
                    "FROM queries WHERE tenant_id = ? OR visibility = 'public' "
                    "ORDER BY created_at ASC "
                    "LIMIT ? OFFSET ?",
                    (tenant_id, limit, offset),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT slug, title, description, sql, created_by, tenant_id, created_at, visibility "
                    "FROM queries WHERE tenant_id = ? "
                    "ORDER BY created_at ASC "
                    "LIMIT ? OFFSET ?",
                    (tenant_id, limit, offset),
                )
        elif created_by is not None:
            cursor = self._conn.execute(
                "SELECT slug, title, description, sql, created_by, tenant_id, created_at, visibility "
                "FROM queries WHERE created_by = ? "
                "ORDER BY created_at ASC "
                "LIMIT ? OFFSET ?",
                (created_by, limit, offset),
            )
        else:
            cursor = self._conn.execute(
                "SELECT slug, title, description, sql, created_by, tenant_id, created_at, visibility "
                "FROM queries ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [dict(row) for row in cursor.fetchall()]
