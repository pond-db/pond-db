"""REST endpoints for the dataset manager (POST/GET/DELETE /datasets)."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Security, UploadFile
from fastapi.security.api_key import APIKeyHeader

from ponddb.dataset_manager import DatasetManager

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> None:
    expected = os.environ.get("POND_API_KEY", "")
    if not key or not key.strip() or key != expected or not expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _info_to_dict(info) -> dict:
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

    @router.post("/datasets", status_code=201, dependencies=[Security(_require_api_key)])
    async def upload_dataset(file: UploadFile) -> dict:
        content = await file.read()
        filename = file.filename or ""
        try:
            info = manager.upload(content=content, original_filename=filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _info_to_dict(info)

    @router.get("/datasets", dependencies=[Security(_require_api_key)])
    async def list_datasets() -> list[dict]:
        return [_info_to_dict(d) for d in manager.list_datasets()]

    @router.get("/datasets/{name}", dependencies=[Security(_require_api_key)])
    async def get_dataset(name: str) -> dict:
        info = manager.get_dataset(name)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {name!r}")
        return _info_to_dict(info)

    @router.delete("/datasets/{name}", dependencies=[Security(_require_api_key)])
    async def delete_dataset(name: str) -> dict:
        removed = manager.delete_dataset(name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {name!r}")
        return {"detail": f"Dataset {name!r} deleted", "name": name}

    return router
