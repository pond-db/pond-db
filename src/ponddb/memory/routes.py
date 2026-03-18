# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""FastAPI routes for agent memory CRUD + feedback."""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from ponddb.auth.jwt_auth import require_auth
from ponddb.memory.access import can_access_memory, can_modify_memory, get_accessible_workgroups
from ponddb.memory.access_log import count_recent_actions, write_access_log
from ponddb.memory.models import (
    GrantCreate,
    MemoryCreate,
    MemoryFeedback,
    MemoryUpdate,
)
from ponddb.memory.search import search_memories
from ponddb.memory.store import MemoryStore


def make_memory_router(store: MemoryStore) -> APIRouter:
    router = APIRouter(tags=["memories"])
    conn = store._conn

    def _wg(auth: dict) -> str:
        """Extract workgroup_id from JWT claims. Falls back to tenant_id."""
        return auth.get("workgroup_id") or auth.get("tenant_id", "default")

    def _agent(auth: dict) -> str:
        return auth.get("agent_id") or auth.get("sub") or "unknown"

    def _trace_id(request: Request) -> Optional[str]:
        tp = request.headers.get("traceparent", "")
        if tp:
            parts = tp.split("-")
            return parts[1] if len(parts) >= 3 else tp
        return None

    # ── POST /memories ─────────────────────────────────────────
    @router.post("/memories", status_code=201)
    async def create_memory(
        body: MemoryCreate,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        agent = body.agent_id

        # Rate limit: 100 writes/min per agent
        recent = count_recent_actions(conn, agent_id=agent, action="write", window_seconds=60)
        if recent >= 100:
            raise HTTPException(429, "Write rate limit: 100/min per agent")

        # Cycle check
        if body.causal_parent_id:
            if store.check_causal_cycle(body.causal_parent_id):
                raise HTTPException(400, "Causal parent would create a cycle (max depth 50)")

        memory = store.create_memory(
            agent_id=agent,
            workgroup_id=wg,
            memory_type=body.memory_type,
            access_scope=body.access_scope,
            content=body.content,
            importance=body.importance,
            memory_key=body.memory_key,
            causal_parent_id=body.causal_parent_id,
            expires_at=body.expires_at,
        )

        latency = (time.perf_counter() - t0) * 1000
        bg.add_task(
            write_access_log,
            conn,
            agent_id=agent,
            workgroup_id=wg,
            action="write",
            memory_ids=[memory["id"]],
            latency_ms=latency,
            trace_id=_trace_id(request),
        )

        return {
            "id": memory["id"],
            "agent_id": agent,
            "memory_type": body.memory_type,
            "created_at": memory["created_at"],
        }

    # ── GET /memories/search ───────────────────────────────────
    @router.get("/memories/search")
    async def search(
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
        agent_id: Optional[str] = Query(None),
        memory_type: Optional[str] = Query(None),
        access_scope: Optional[str] = Query(None),
        min_importance: Optional[float] = Query(None),
        min_utility: Optional[float] = Query(None),
        since: Optional[str] = Query(None),
        until: Optional[str] = Query(None),
        content_contains: Optional[str] = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        caller = _agent(auth)

        accessible = get_accessible_workgroups(conn, wg, caller_agent_id=caller)
        granted = [e for e in accessible if e["grant_id"] is not None]

        results = search_memories(
            conn,
            wg,
            agent_id=agent_id,
            caller_agent_id=caller,
            memory_type=memory_type,
            access_scope=access_scope,
            min_importance=min_importance,
            min_utility=min_utility,
            since=since,
            until=until,
            content_contains=content_contains,
            limit=limit,
            granted_workgroups=granted,
        )

        latency = (time.perf_counter() - t0) * 1000
        grant_ids = list({r.get("_grant_id") for r in results if r.get("_grant_id")})
        # Strip internal fields
        for r in results:
            r.pop("_grant_id", None)
            r.pop("_source_workgroup_id", None)

        bg.add_task(
            write_access_log,
            conn,
            agent_id=caller,
            workgroup_id=wg,
            action="search",
            result_count=len(results),
            latency_ms=latency,
            grant_id=grant_ids[0] if grant_ids else None,
            trace_id=_trace_id(request),
        )
        return results

    # ── GET /memories/{id} ─────────────────────────────────────
    @router.get("/memories/{memory_id}")
    async def get_memory(
        memory_id: str,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        caller = _agent(auth)

        memory = store.get_memory(memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")

        if not can_access_memory(conn, memory, wg, caller):
            raise HTTPException(403, "Access denied")

        latency = (time.perf_counter() - t0) * 1000
        bg.add_task(
            write_access_log,
            conn,
            agent_id=caller,
            workgroup_id=wg,
            action="read",
            memory_ids=[memory_id],
            latency_ms=latency,
            trace_id=_trace_id(request),
        )
        return memory

    # ── PUT /memories/{id} ─────────────────────────────────────
    @router.put("/memories/{memory_id}")
    async def update_memory(
        memory_id: str,
        body: MemoryUpdate,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        caller = _agent(auth)
        is_admin = auth.get("role") == "admin"

        memory = store.get_memory(memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")
        if not can_modify_memory(memory, wg, caller, is_admin):
            raise HTTPException(403, "Only creating agent or admin can update")

        kwargs = {}
        if body.content is not None:
            kwargs["content"] = body.content
        if body.importance is not None:
            kwargs["importance"] = body.importance
        updated = store.update_memory(memory_id, **kwargs)

        latency = (time.perf_counter() - t0) * 1000
        bg.add_task(
            write_access_log,
            conn,
            agent_id=caller,
            workgroup_id=wg,
            action="update",
            memory_ids=[memory_id],
            latency_ms=latency,
            trace_id=_trace_id(request),
        )
        return updated

    # ── DELETE /memories/{id} ──────────────────────────────────
    @router.delete("/memories/{memory_id}")
    async def delete_memory(
        memory_id: str,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        caller = _agent(auth)
        is_admin = auth.get("role") == "admin"

        memory = store.get_memory(memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")
        if not can_modify_memory(memory, wg, caller, is_admin):
            raise HTTPException(403, "Only creating agent or admin can delete")

        result = store.soft_delete_memory(memory_id)

        latency = (time.perf_counter() - t0) * 1000
        bg.add_task(
            write_access_log,
            conn,
            agent_id=caller,
            workgroup_id=wg,
            action="delete",
            memory_ids=[memory_id],
            latency_ms=latency,
            trace_id=_trace_id(request),
        )
        return {"id": memory_id, "deleted_at": result.get("deleted_at")}

    # ── POST /memories/{id}/feedback ───────────────────────────
    @router.post("/memories/{memory_id}/feedback")
    async def memory_feedback(
        memory_id: str,
        body: MemoryFeedback,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        wg = _wg(auth)
        caller = _agent(auth)

        memory = store.get_memory(memory_id)
        if memory is None:
            raise HTTPException(404, "Memory not found")

        # Rate limit: 10 feedback per memory per hour
        recent = count_recent_actions(
            conn,
            action="feedback",
            memory_id=memory_id,
            window_seconds=3600,
        )
        if recent >= 10:
            raise HTTPException(429, "Feedback rate limit: 10/hour per memory")

        result = store.update_utility(memory_id, body.reward)

        latency = (time.perf_counter() - t0) * 1000
        bg.add_task(
            write_access_log,
            conn,
            agent_id=caller,
            workgroup_id=wg,
            action="feedback",
            memory_ids=[memory_id],
            latency_ms=latency,
            trace_id=_trace_id(request),
        )
        return {
            "id": memory_id,
            "old_utility": result["old_utility"],
            "new_utility": result["new_utility"],
        }

    # ── POST /memory-grants ───────────────────────────────────
    @router.post("/memory-grants", status_code=201)
    async def create_grant(
        body: GrantCreate,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        from ponddb.memory.grants import create_grant as _create

        is_admin = auth.get("role") == "admin"
        wg = _wg(auth)

        # Only admin or grantor workgroup owner can create grants
        if not is_admin and wg != body.grantor_workgroup_id:
            raise HTTPException(403, "Only namespace admin or grantor WG admin can create grants")

        # At least one grantee
        if not body.grantee_workgroup_id and not body.grantee_agent_id:
            raise HTTPException(400, "Must specify grantee_workgroup_id or grantee_agent_id")

        # Can't grant to yourself
        if body.grantee_workgroup_id == body.grantor_workgroup_id:
            raise HTTPException(400, "Cannot grant access to your own workgroup")

        grant = _create(
            conn,
            grantor_workgroup_id=body.grantor_workgroup_id,
            grantee_workgroup_id=body.grantee_workgroup_id,
            grantee_agent_id=body.grantee_agent_id,
            memory_type_filter=body.memory_type_filter,
            min_importance=body.min_importance,
            permission=body.permission,
            valid_until=body.valid_until,
            created_by=_agent(auth),
        )

        bg.add_task(
            write_access_log,
            conn,
            agent_id=_agent(auth),
            workgroup_id=wg,
            action="write",
            grant_id=grant["id"],
            trace_id=_trace_id(request),
        )
        return grant

    # ── DELETE /memory-grants/{id} ─────────────────────────────
    @router.delete("/memory-grants/{grant_id}")
    async def revoke_grant(
        grant_id: str,
        request: Request,
        bg: BackgroundTasks,
        auth: dict = Depends(require_auth),
    ) -> dict[str, Any]:
        from ponddb.memory.grants import delete_grant, get_grant

        is_admin = auth.get("role") == "admin"
        wg = _wg(auth)

        grant = get_grant(conn, grant_id)
        if grant is None:
            raise HTTPException(404, "Grant not found")

        if not is_admin and wg != grant["grantor_workgroup_id"]:
            raise HTTPException(403, "Only namespace admin or grantor WG admin can revoke grants")

        delete_grant(conn, grant_id)

        bg.add_task(
            write_access_log,
            conn,
            agent_id=_agent(auth),
            workgroup_id=wg,
            action="delete",
            grant_id=grant_id,
            trace_id=_trace_id(request),
        )
        return {"detail": "revoked", "grant_id": grant_id}

    return router
