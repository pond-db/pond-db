# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""ComputeTracker — measures wall time and memory delta per DuckDB query."""

import hashlib
import resource
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import duckdb


@dataclass
class ComputeSample:
    session_id: str
    query_hash: str
    wall_ms: float
    mem_delta_kb: float
    timestamp: datetime


def _hash_sql(sql: str) -> str:
    """SHA-256 hex digest of SQL text."""
    return hashlib.sha256(sql.encode()).hexdigest()


def _drive(coro) -> None:
    """Drive a fake-async coroutine (synchronous sqlite3 internals)."""
    try:
        coro.send(None)
    except StopIteration:
        pass


def _mem_kb() -> float:
    """Current process max RSS in KB (Linux: already KB; macOS: bytes → KB)."""
    try:
        import sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return rss / 1024.0
        return float(rss)
    except Exception:
        return 0.0


class ComputeTracker:
    """Wraps DuckDB query execution to capture wall time and approximate memory delta."""

    def __init__(self, store=None) -> None:
        self.store = store  # Optional[MetadataStore]

    def track_query(
        self,
        session_id: str,
        sql: str,
        conn: duckdb.DuckDBPyConnection,
    ) -> ComputeSample:
        """Execute *sql* on *conn*, record timing/memory, persist if store is set."""
        query_hash = _hash_sql(sql)
        mem_before = _mem_kb()

        start = time.perf_counter()
        conn.execute(sql)
        wall_ms = (time.perf_counter() - start) * 1000.0

        mem_delta_kb = max(0.0, _mem_kb() - mem_before)
        timestamp = datetime.now(timezone.utc)

        sample = ComputeSample(
            session_id=session_id,
            query_hash=query_hash,
            wall_ms=wall_ms,
            mem_delta_kb=mem_delta_kb,
            timestamp=timestamp,
        )

        if self.store is not None:
            _drive(self.store.log_compute_sample(sample))

        return sample
