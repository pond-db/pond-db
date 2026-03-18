# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Background tasks: working memory cleanup (60s), utility decay (24h)."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ponddb.memory.access_log import write_access_log


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryCleanupTask:
    """Periodically deletes expired working memories."""

    def __init__(self, conn: sqlite3.Connection, interval: float = 60.0) -> None:
        self._conn = conn
        self._interval = interval
        self._last_run: Optional[str] = None
        self._last_count: int = 0
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[memory-cleanup] error: {e}")

    def run_once(self) -> int:
        """Run cleanup synchronously. Returns count of deleted memories."""
        now = _now_iso()
        try:
            cursor = self._conn.execute(
                "UPDATE agent_memories SET deleted_at = ? "
                "WHERE memory_type = 'working' AND expires_at IS NOT NULL "
                "AND expires_at < ? AND deleted_at IS NULL",
                (now, now),
            )
            count = cursor.rowcount
            self._conn.commit()
        except Exception:
            count = 0
        self._last_run = now
        self._last_count = count

        if count > 0:
            try:
                write_access_log(
                    self._conn,
                    agent_id="system",
                    workgroup_id="system",
                    action="cleanup",
                    result_count=count,
                )
            except Exception:
                pass

        return count

    def health(self) -> dict:
        return {
            "last_run": self._last_run,
            "last_deleted_count": self._last_count,
            "status": "ok" if self._last_run else "not_started",
        }


class UtilityDecayTask:
    """Decays utility for memories not accessed in 7+ days."""

    def __init__(self, conn: sqlite3.Connection, interval: float = 86400.0) -> None:
        self._conn = conn
        self._interval = interval
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[utility-decay] error: {e}")

    def run_once(self) -> int:
        """Apply utility decay. Returns count of affected memories."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        try:
            cursor = self._conn.execute(
                "UPDATE agent_memories SET utility = MAX(0.1, utility * 0.99) "
                "WHERE deleted_at IS NULL "
                "AND (last_accessed_at IS NULL OR last_accessed_at < ?)",
                (cutoff,),
            )
            count = cursor.rowcount
            self._conn.commit()
        except Exception:
            count = 0
        return count
