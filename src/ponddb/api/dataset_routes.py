# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""REST endpoints for the dataset manager (POST/GET/DELETE /datasets).

Auth: accepts Bearer JWT, X-API-Key header, or session cookie (via require_auth).
GET /datasets serves HTML for browsers (Accept: text/html) and JSON for API clients.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ponddb.store.dataset_manager import DatasetManager
from ponddb.auth.jwt_auth import require_auth

_templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def _info_to_dict(info: Any) -> dict:
    return {
        "name": info.name,
        "format": info.format,
        "size_bytes": info.size_bytes,
        "row_count": info.row_count,
        "columns": info.columns,
        "created_at": info.created_at,
    }


def make_dataset_router(manager: DatasetManager) -> APIRouter:
    router = APIRouter()

    @router.post("/datasets", status_code=201)
    async def upload_dataset(
        file: UploadFile, _auth: dict = Depends(require_auth),
    ) -> dict:
        content = await file.read()
        filename = file.filename or ""
        try:
            info = manager.upload(content=content, original_filename=filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _info_to_dict(info)

    @router.get("/datasets")
    async def list_datasets(
        request: Request, _auth: dict = Depends(require_auth),
    ) -> Any:
        datasets = [_info_to_dict(d) for d in manager.list_datasets()]

        # Content negotiation: HTML for browsers, JSON for API clients
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            from ponddb.api.website_routes import _get_session, _build_current_user
            session = _get_session(request)
            if not session:
                return RedirectResponse(url="/login", status_code=302)
            return _templates.TemplateResponse(
                request, "datasets.html",
                {
                    "datasets": datasets,
                    "current_user": _build_current_user(session),
                    "active_page": "datasets",
                    "workgroups_nav": [],
                },
            )

        return datasets

    @router.get("/datasets/{name}")
    async def get_dataset(
        name: str, _auth: dict = Depends(require_auth),
    ) -> dict:
        info = manager.get_dataset(name)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {name!r}")
        return _info_to_dict(info)

    @router.delete("/datasets/{name}")
    async def delete_dataset(
        name: str, _auth: dict = Depends(require_auth),
    ) -> dict:
        removed = manager.delete_dataset(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {name!r}")
        return {"detail": f"Dataset {name!r} deleted", "name": name}

    return router
