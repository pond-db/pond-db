# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""MemoryStore — SQLite persistence for agent memories, grants, and access log.

Follows the same synchronous-sqlite-with-async-wrappers pattern as MetadataStore.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class MemoryStore:
    """SQLite store for agent_memories, memory_grants, memory_access_log."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def initialize_blocking(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_memories (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                workgroup_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                access_scope TEXT NOT NULL DEFAULT 'private',
                content TEXT NOT NULL,
                memory_key TEXT,
                importance REAL NOT NULL DEFAULT 0.5,
                utility REAL NOT NULL DEFAULT 0.5,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT,
                causal_parent_id TEXT,
                linked_memory_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                deleted_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_mem_agent_wg
                ON agent_memories(agent_id, workgroup_id)
                WHERE deleted_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_mem_type_wg
                ON agent_memories(memory_type, workgroup_id)
                WHERE deleted_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_mem_utility
                ON agent_memories(utility DESC)
                WHERE deleted_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_mem_created
                ON agent_memories(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_mem_expiry
                ON agent_memories(expires_at)
                WHERE expires_at IS NOT NULL AND memory_type = 'working';
            CREATE INDEX IF NOT EXISTS idx_mem_causal
                ON agent_memories(causal_parent_id)
                WHERE causal_parent_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_key_wg
                ON agent_memories(workgroup_id, memory_key)
                WHERE memory_key IS NOT NULL AND deleted_at IS NULL;

            CREATE TABLE IF NOT EXISTS memory_grants (
                id TEXT PRIMARY KEY,
                grantor_workgroup_id TEXT NOT NULL,
                grantee_agent_id TEXT,
                grantee_workgroup_id TEXT,
                memory_type_filter TEXT,
                min_importance REAL NOT NULL DEFAULT 0.0,
                permission TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_grants_grantor
                ON memory_grants(grantor_workgroup_id);
            CREATE INDEX IF NOT EXISTS idx_grants_grantee_wg
                ON memory_grants(grantee_workgroup_id);
            CREATE INDEX IF NOT EXISTS idx_grants_grantee_agent
                ON memory_grants(grantee_agent_id);

            CREATE TABLE IF NOT EXISTS memory_access_log (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                workgroup_id TEXT NOT NULL,
                action TEXT NOT NULL,
                memory_ids TEXT,
                query_text TEXT,
                result_count INTEGER,
                grant_id TEXT,
                source_workgroup_id TEXT,
                execution_id TEXT,
                trace_id TEXT,
                span_id TEXT,
                latency_ms REAL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_type TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mal_agent
                ON memory_access_log(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_mal_action
                ON memory_access_log(action, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_mal_trace
                ON memory_access_log(trace_id)
                WHERE trace_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_mal_grant
                ON memory_access_log(grant_id)
                WHERE grant_id IS NOT NULL;
        """)
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {k: row[k] for k in row.keys()}

    # ------------------------------------------------------------------
    # agent_memories CRUD
    # ------------------------------------------------------------------

    def create_memory(self, **kwargs: Any) -> dict[str, Any]:
        mid = kwargs.get("id") or _new_id()
        now = _now_iso()
        content = kwargs["content"]
        if not isinstance(content, str):
            content = json.dumps(content)
        linked = kwargs.get("linked_memory_ids", [])
        if not isinstance(linked, str):
            linked = json.dumps(linked)

        # Upsert if memory_key provided
        memory_key = kwargs.get("memory_key")
        if memory_key:
            existing = self._conn.execute(
                "SELECT id FROM agent_memories WHERE workgroup_id = ? AND memory_key = ? AND deleted_at IS NULL",
                (kwargs["workgroup_id"], memory_key),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE agent_memories SET content = ?, importance = ?, updated_at = ? WHERE id = ?",
                    (content, kwargs.get("importance", 0.5), now, existing["id"]),
                )
                self._conn.commit()
                return self.get_memory(existing["id"])

        self._conn.execute(
            """INSERT INTO agent_memories
               (id, agent_id, workgroup_id, memory_type, access_scope, content,
                memory_key, importance, utility, linked_memory_ids,
                causal_parent_id, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid, kwargs["agent_id"], kwargs["workgroup_id"],
                kwargs["memory_type"], kwargs.get("access_scope", "private"),
                content, memory_key,
                kwargs.get("importance", 0.5), kwargs.get("utility", 0.5),
                linked, kwargs.get("causal_parent_id"),
                now, now, kwargs.get("expires_at"),
            ),
        )
        self._conn.commit()
        return self.get_memory(mid)

    def get_memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM agent_memories WHERE id = ? AND deleted_at IS NULL",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        d = self._row_to_dict(row)
        d["content"] = json.loads(d["content"]) if isinstance(d["content"], str) else d["content"]
        d["linked_memory_ids"] = json.loads(d["linked_memory_ids"]) if isinstance(d["linked_memory_ids"], str) else d["linked_memory_ids"]
        return d

    def update_memory(self, memory_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        sets, vals = [], []
        if "content" in kwargs:
            c = kwargs["content"]
            sets.append("content = ?")
            vals.append(json.dumps(c) if not isinstance(c, str) else c)
        if "importance" in kwargs:
            sets.append("importance = ?")
            vals.append(kwargs["importance"])
        if not sets:
            return self.get_memory(memory_id)
        sets.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(memory_id)
        self._conn.execute(
            f"UPDATE agent_memories SET {', '.join(sets)} WHERE id = ? AND deleted_at IS NULL",
            vals,
        )
        self._conn.commit()
        return self.get_memory(memory_id)

    def soft_delete_memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        now = _now_iso()
        self._conn.execute(
            "UPDATE agent_memories SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, memory_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM agent_memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def update_utility(self, memory_id: str, reward: float) -> Optional[dict[str, Any]]:
        """Apply MemRL utility update: utility = clamp(utility + 0.1*(reward - utility), 0.1, 0.9)."""
        row = self._conn.execute(
            "SELECT utility FROM agent_memories WHERE id = ? AND deleted_at IS NULL",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        old = row["utility"]
        new = max(0.1, min(0.9, old + 0.1 * (reward - old)))
        self._conn.execute(
            "UPDATE agent_memories SET utility = ?, updated_at = ? WHERE id = ?",
            (new, _now_iso(), memory_id),
        )
        self._conn.commit()
        return {"old_utility": old, "new_utility": new, **self.get_memory(memory_id)}

    def check_causal_cycle(self, parent_id: str, new_id: Optional[str] = None) -> bool:
        """Return True if adding new_id with causal_parent_id=parent_id creates a cycle."""
        visited = set()
        current = parent_id
        depth = 0
        while current and depth < 50:
            if current == new_id:
                return True
            if current in visited:
                return True
            visited.add(current)
            row = self._conn.execute(
                "SELECT causal_parent_id FROM agent_memories WHERE id = ?", (current,)
            ).fetchone()
            current = row["causal_parent_id"] if row else None
            depth += 1
        return False
