# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""REST endpoints for the named query store — JWT tenant-aware."""

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from ponddb.auth.jwt_auth import require_auth
from ponddb.store.metadata_store import MetadataStore
from ponddb.store.query_store import DuplicateQueryError, QueryNotFoundError


class SaveQueryRequest(BaseModel):
    title: str
    description: str = ""
    sql: str
    created_by: Optional[str] = None  # derived from JWT if omitted
    visibility: Literal["public", "private"] = "private"


def make_query_router(store: MetadataStore) -> APIRouter:
    """Return a router with /queries endpoints bound to *store*."""
    router = APIRouter()

    @router.post("/queries", status_code=201)
    async def create_query(
        req: SaveQueryRequest,
        _auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        tenant_id: str = _auth.get("tenant_id", "default")
        created_by = req.created_by or tenant_id
        try:
            slug = await store.save_query(
                title=req.title,
                description=req.description,
                sql=req.sql,
                created_by=created_by,
                tenant_id=tenant_id,
                visibility=req.visibility,
            )
        except DuplicateQueryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        result = await store.get_query_by_slug(slug, tenant_id=tenant_id)
        return result

    @router.get("/queries")
    async def list_queries(
        _auth: dict = Depends(require_auth),
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        tenant_id: str = _auth.get("tenant_id", "default")
        return await store.list_queries(
            tenant_id=tenant_id, include_public=True, limit=limit, offset=offset
        )

    _SLUG_RE = r"^[a-z0-9][a-z0-9-]*$"

    @router.get("/queries/{slug}")
    async def get_query(
        slug: Annotated[str, Path(pattern=_SLUG_RE, max_length=255)],
        _auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        tenant_id: str = _auth.get("tenant_id", "default")
        try:
            return await store.get_query_by_slug(slug, tenant_id=tenant_id)
        except QueryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return router
