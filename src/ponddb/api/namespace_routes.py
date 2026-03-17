# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Namespace + Workgroup CRUD routes for PondDB admin API."""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ponddb.security import audit_log
from ponddb.auth.jwt_auth import require_admin


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class NamespaceCreate(BaseModel):
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v


class NamespaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class WorkgroupQuota(BaseModel):
    max_sessions: Optional[int] = None
    max_query_duration_ms: Optional[int] = None
    max_result_mb: Optional[int] = None

    @field_validator("max_sessions")
    @classmethod
    def max_sessions_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("max_sessions must be a positive integer (> 0)")
        return v


class WorkgroupCreate(BaseModel):
    name: str
    namespace_id: str
    description: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    quota: Optional[WorkgroupQuota] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v


class WorkgroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    quota: Optional[WorkgroupQuota] = None


# ---------------------------------------------------------------------------
# Module-level utility functions
# ---------------------------------------------------------------------------


def check_and_reserve_session_slot(workgroup: dict[str, Any]) -> dict[str, Any]:
    """Check if a session slot is available within the workgroup quota.

    Raises ValueError if the quota is exceeded.
    Returns a confirmation dict if the slot is available.
    """
    quota = workgroup.get("quota")
    if quota is None:
        return {"reserved": True, "workgroup_id": workgroup.get("id")}

    max_sessions = quota.get("max_sessions") if isinstance(quota, dict) else None
    if max_sessions is None:
        return {"reserved": True, "workgroup_id": workgroup.get("id")}

    active = workgroup.get("active_sessions", 0)
    if active >= max_sessions:
        raise ValueError(
            f"Session quota exceeded: workgroup has reached the limit of "
            f"{max_sessions} active sessions ({active} currently active)."
        )
    return {"reserved": True, "workgroup_id": workgroup.get("id")}


def reconcile_workgroup_usage(
    workgroup_id: str,
    workgroups: dict[str, Any],
    session_workgroups: dict[str, str],
) -> dict[str, Any]:
    """Reconcile active_sessions count from actual session tracking data."""
    if workgroup_id not in workgroups:
        raise KeyError(f"Workgroup not found: {workgroup_id}")
    actual_count = sum(1 for wg_id in session_workgroups.values() if wg_id == workgroup_id)
    workgroups[workgroup_id]["active_sessions"] = actual_count
    return {"workgroup_id": workgroup_id, "active_sessions": actual_count, "reconciled": True}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_namespace_workgroup_router(
    workgroups_ref: Optional[dict[str, Any]] = None,
    session_workgroups_ref: Optional[dict[str, str]] = None,
    namespaces_ref: Optional[dict[str, Any]] = None,
) -> APIRouter:
    """Return a router with in-memory state. Pass external dicts for shared access."""
    router = APIRouter()

    _namespaces: dict[str, dict[str, Any]] = namespaces_ref if namespaces_ref is not None else {}
    _workgroups: dict[str, dict[str, Any]] = workgroups_ref if workgroups_ref is not None else {}
    _session_wgs: dict[str, str] = (
        session_workgroups_ref if session_workgroups_ref is not None else {}
    )

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -----------------------------------------------------------------------
    # Namespace endpoints
    # -----------------------------------------------------------------------

    @router.post("/namespaces", status_code=201)
    async def create_namespace(
        body: NamespaceCreate,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        for ns in _namespaces.values():
            if ns["name"] == body.name:
                raise HTTPException(
                    status_code=409, detail=f"Namespace '{body.name}' already exists"
                )
        now = _now()
        ns_id = str(uuid4())
        record: dict[str, Any] = {
            "id": ns_id,
            "name": body.name,
            "description": body.description if body.description is not None else "",
            "created_at": now,
            "updated_at": now,
        }
        _namespaces[ns_id] = record
        return record

    @router.get("/namespaces")
    async def list_namespaces(
        _admin: dict = Depends(require_admin),
    ) -> list[dict[str, Any]]:
        return list(_namespaces.values())

    @router.get("/namespaces/{ns_id}")
    async def get_namespace(
        ns_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if ns_id not in _namespaces:
            raise HTTPException(status_code=404, detail=f"Namespace not found: {ns_id}")
        return _namespaces[ns_id]

    @router.put("/namespaces/{ns_id}")
    async def update_namespace(
        ns_id: str,
        body: NamespaceUpdate,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if ns_id not in _namespaces:
            raise HTTPException(status_code=404, detail=f"Namespace not found: {ns_id}")
        record = _namespaces[ns_id]
        if body.name is not None:
            record["name"] = body.name
        if body.description is not None:
            record["description"] = body.description
        record["updated_at"] = _now()
        return record

    @router.delete("/namespaces/{ns_id}")
    async def delete_namespace(
        ns_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if ns_id not in _namespaces:
            raise HTTPException(status_code=404, detail=f"Namespace not found: {ns_id}")
        del _namespaces[ns_id]
        to_remove = [wid for wid, wg in _workgroups.items() if wg["namespace_id"] == ns_id]
        for wid in to_remove:
            del _workgroups[wid]
        return {"detail": "deleted"}

    # -----------------------------------------------------------------------
    # Workgroup endpoints
    # -----------------------------------------------------------------------

    @router.post("/workgroups", status_code=201)
    async def create_workgroup(
        body: WorkgroupCreate,
        admin_claims: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if body.namespace_id not in _namespaces:
            raise HTTPException(status_code=404, detail=f"Namespace not found: {body.namespace_id}")
        now = _now()
        wg_id = str(uuid4())
        quota_dict = body.quota.model_dump() if body.quota is not None else None
        record: dict[str, Any] = {
            "id": wg_id,
            "name": body.name,
            "namespace_id": body.namespace_id,
            "description": body.description if body.description is not None else "",
            "config": body.config if body.config is not None else {},
            "quota": quota_dict,
            "active_sessions": 0,
            "created_at": now,
            "updated_at": now,
        }
        _workgroups[wg_id] = record
        tenant_id: str = admin_claims.get("tenant_id", "default")
        await audit_log.log_event(
            None,
            "workgroup_created",
            tenant_id=tenant_id,
            detail=f"created workgroup {body.name} in namespace {body.namespace_id}",
        )
        return record

    @router.get("/workgroups")
    async def list_workgroups(
        namespace_id: Optional[str] = None,
        _admin: dict = Depends(require_admin),
    ) -> list[dict[str, Any]]:
        result = list(_workgroups.values())
        if namespace_id is not None:
            result = [wg for wg in result if wg["namespace_id"] == namespace_id]
        return result

    @router.get("/workgroups/{wg_id}/usage")
    async def get_workgroup_usage(
        wg_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if wg_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {wg_id}")
        wg = _workgroups[wg_id]
        quota = wg.get("quota")
        active = wg.get("active_sessions", 0)
        max_s: Optional[int] = quota.get("max_sessions") if quota else None
        available: Any = (max_s - active) if max_s is not None else None
        util_pct = round(active / max_s * 100.0, 4) if max_s else 0.0
        return {
            "workgroup_id": wg_id,
            "quota": quota,
            "usage": {"active_sessions": active},
            "available_slots": available,
            "utilization_pct": util_pct,
        }

    @router.get("/workgroups/{wg_id}")
    async def get_workgroup(
        wg_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if wg_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {wg_id}")
        return _workgroups[wg_id]

    @router.put("/workgroups/{wg_id}")
    async def update_workgroup(
        wg_id: str,
        body: WorkgroupUpdate,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if wg_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {wg_id}")
        record = _workgroups[wg_id]
        if body.name is not None:
            record["name"] = body.name
        if body.description is not None:
            record["description"] = body.description
        if body.config is not None:
            record["config"] = body.config
        if body.quota is not None:
            record["quota"] = body.quota.model_dump()
        record["updated_at"] = _now()
        return record

    @router.delete("/workgroups/{wg_id}")
    async def delete_workgroup(
        wg_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if wg_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {wg_id}")
        del _workgroups[wg_id]
        return {"detail": "deleted"}

    @router.post("/workgroups/{wg_id}/reconcile")
    async def reconcile_workgroup(
        wg_id: str,
        _admin: dict = Depends(require_admin),
    ) -> dict[str, Any]:
        if wg_id not in _workgroups:
            raise HTTPException(status_code=404, detail=f"Workgroup not found: {wg_id}")
        return reconcile_workgroup_usage(wg_id, _workgroups, _session_wgs)

    return router
