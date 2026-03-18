# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Pydantic request/response models for the agent memory API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

MEMORY_TYPES = ("working", "episodic", "semantic", "procedural", "shared")
ACCESS_SCOPES = ("private", "workgroup", "namespace")
GRANT_PERMISSIONS = ("read", "write", "read_write")


class MemoryCreate(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=200)
    memory_type: Literal["working", "episodic", "semantic", "procedural", "shared"]
    content: dict[str, Any]
    access_scope: Literal["private", "workgroup", "namespace"] = "private"
    importance: float = Field(0.5, ge=0.0, le=1.0)
    memory_key: Optional[str] = None
    causal_parent_id: Optional[str] = None
    expires_at: Optional[str] = None


class MemoryResponse(BaseModel):
    id: str
    agent_id: str
    memory_type: str
    created_at: str
    workgroup_id: Optional[str] = None


class MemoryFull(BaseModel):
    id: str
    agent_id: str
    workgroup_id: str
    memory_type: str
    access_scope: str
    content: Any
    memory_key: Optional[str] = None
    importance: float
    utility: float
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    causal_parent_id: Optional[str] = None
    linked_memory_ids: list[str] = []
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None
    deleted_at: Optional[str] = None


class MemoryUpdate(BaseModel):
    content: Optional[dict[str, Any]] = None
    importance: Optional[float] = Field(None, ge=0.0, le=1.0)


class MemoryFeedback(BaseModel):
    reward: float = Field(..., ge=-1.0, le=1.0)


class GrantCreate(BaseModel):
    grantor_workgroup_id: str
    grantee_workgroup_id: Optional[str] = None
    grantee_agent_id: Optional[str] = None
    memory_type_filter: Optional[Literal["working", "episodic", "semantic", "procedural", "shared"]] = None
    min_importance: float = Field(0.0, ge=0.0, le=1.0)
    permission: Literal["read", "write", "read_write"]
    valid_until: Optional[str] = None

    @field_validator("grantee_workgroup_id", "grantee_agent_id", mode="after")
    @classmethod
    def at_least_one_grantee(cls, v: Optional[str], info) -> Optional[str]:
        # Validation happens at model level in model_validator
        return v


class GrantResponse(BaseModel):
    id: str
    grantor_workgroup_id: str
    grantee_workgroup_id: Optional[str] = None
    grantee_agent_id: Optional[str] = None
    permission: str
    created_at: str
