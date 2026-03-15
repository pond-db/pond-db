"""REST endpoints for the named query store."""

import os
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from ponddb.metadata_store import MetadataStore
from ponddb.query_store import DuplicateQueryError, QueryNotFoundError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> None:
    """Validate X-API-Key against POND_API_KEY env var."""
    expected = os.environ.get("POND_API_KEY", "")
    if not key or not key.strip() or key != expected or not expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class SaveQueryRequest(BaseModel):
    title: str
    description: str = ""
    sql: str
    created_by: str
    visibility: Literal["public", "private"] = "private"


def make_query_router(store: MetadataStore) -> APIRouter:
    """Return a router with /queries endpoints bound to *store*."""
    router = APIRouter()

    @router.post("/queries", status_code=201, dependencies=[Security(_require_api_key)])
    async def create_query(req: SaveQueryRequest) -> dict[str, Any]:
        try:
            slug = await store.save_query(
                title=req.title,
                description=req.description,
                sql=req.sql,
                created_by=req.created_by,
                visibility=req.visibility,
            )
        except DuplicateQueryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        result = await store.get_query_by_slug(slug)
        return result

    @router.get("/queries", dependencies=[Security(_require_api_key)])
    async def list_queries(
        created_by: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await store.list_queries(
            created_by=created_by, limit=limit, offset=offset
        )

    @router.get("/queries/{slug}", dependencies=[Security(_require_api_key)])
    async def get_query(slug: str) -> dict[str, Any]:
        try:
            return await store.get_query_by_slug(slug)
        except QueryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
