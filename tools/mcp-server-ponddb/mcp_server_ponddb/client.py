# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Thin httpx wrapper for PondDB memory API."""

from __future__ import annotations

from typing import Any, Optional

import httpx


class PondDBClient:
    """Synchronous httpx client targeting PondDB memory endpoints."""

    def __init__(self, base_url: str, api_key: str, workgroup: str = "default") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }
        self._workgroup = workgroup

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def remember(
        self,
        agent_id: str,
        memory_type: str,
        content: dict[str, Any],
        *,
        access_scope: str = "private",
        importance: float = 0.5,
        memory_key: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "memory_type": memory_type,
            "content": content,
            "access_scope": access_scope,
            "importance": importance,
        }
        if memory_key:
            body["memory_key"] = memory_key
        resp = httpx.post(self._url("/memories"), json=body, headers=self._headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def recall(
        self,
        *,
        agent_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        content_contains: Optional[str] = None,
        min_importance: Optional[float] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        if memory_type:
            params["memory_type"] = memory_type
        if content_contains:
            params["content_contains"] = content_contains
        if min_importance is not None:
            params["min_importance"] = min_importance
        resp = httpx.get(self._url("/memories/search"), params=params, headers=self._headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def query(self, sql: str, session_id: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"sql": sql}
        if session_id:
            body["session_id"] = session_id
        resp = httpx.post(self._url("/pondapi/execute"), json=body, headers=self._headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def forget(self, memory_id: str) -> dict[str, Any]:
        resp = httpx.delete(self._url(f"/memories/{memory_id}"), headers=self._headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def feedback(self, memory_id: str, reward: float) -> dict[str, Any]:
        resp = httpx.post(
            self._url(f"/memories/{memory_id}/feedback"),
            json={"reward": reward},
            headers=self._headers,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
