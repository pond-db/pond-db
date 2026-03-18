#!/usr/bin/env python3
"""Benchmark 4: Analytical queries that competitors cannot do."""

from __future__ import annotations

import asyncio
import random
import time
import uuid

from helpers import (
    BenchmarkResult,
    build_parser,
    create_session,
    destroy_session,
    execute_query,
    fmt_ms,
    make_client,
)

NUM_AGENTS = 10
NUM_MEMORIES = 10_000
NUM_EXECUTIONS = 1_000
NUM_DAYS = 7
TOPICS = [
    "planning", "debugging", "research", "summarization",
    "code_review", "testing", "deployment", "monitoring",
    "architecture", "documentation",
]

QUERIES = [
    (
        "Q1",
        "Top agents by memory count",
        "SELECT agent_id, COUNT(*) AS total "
        "FROM bench_memories GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
    ),
    (
        "Q2",
        "Daily memory creation",
        "SELECT DATE_TRUNC('day', created_at) AS day, COUNT(*) "
        "FROM bench_memories GROUP BY 1 ORDER BY 1",
    ),
    (
        "Q3",
        "Context before failures",
        "SELECT m.agent_id, LEFT(m.content, 60) AS content, e.error_message "
        "FROM bench_memories m "
        "JOIN bench_executions e ON m.agent_id = e.agent_id "
        "WHERE e.status = 'failed' "
        "AND m.created_at < e.submitted_at "
        "AND m.created_at > e.submitted_at - INTERVAL '5 minutes' "
        "LIMIT 50",
    ),
    (
        "Q4",
        "Cross-agent topic overlap",
        "SELECT a.agent_id, b.agent_id, COUNT(*) AS shared_topics "
        "FROM bench_memories a JOIN bench_memories b "
        "ON a.content_topic = b.content_topic AND a.agent_id != b.agent_id "
        "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10",
    ),
    (
        "Q5",
        "Growth projection (window)",
        "SELECT day, daily_count, "
        "SUM(daily_count) OVER (ORDER BY day) AS cumulative, "
        "AVG(daily_count) OVER (ORDER BY day ROWS 6 PRECEDING) AS rolling_7d "
        "FROM ("
        "  SELECT DATE_TRUNC('day', created_at) AS day, COUNT(*) AS daily_count "
        "  FROM bench_memories GROUP BY 1"
        ") sub ORDER BY day",
    ),
]


async def _setup(client, session_id: str) -> None:
    """Create tables and load test data."""
    await execute_query(
        client,
        session_id,
        "CREATE TABLE bench_memories ("
        "  agent_id INTEGER, content VARCHAR, content_topic VARCHAR, "
        "  created_at TIMESTAMP"
        ")",
    )
    await execute_query(
        client,
        session_id,
        "CREATE TABLE bench_executions ("
        "  agent_id INTEGER, status VARCHAR, error_message VARCHAR, "
        "  submitted_at TIMESTAMP"
        ")",
    )

    # Insert memories in batches (respects 50 KB SQL limit).
    # Topic is assigned independently of agent so that agents share topics
    # and Q4 (cross-agent topic overlap) returns meaningful results.
    batch = 200
    for offset in range(0, NUM_MEMORIES, batch):
        values = ", ".join(
            f"({i % NUM_AGENTS}, "
            f"'Memory content {i} from agent {i % NUM_AGENTS}', "
            f"'{TOPICS[(i + i // NUM_AGENTS) % len(TOPICS)]}', "
            f"TIMESTAMP '2026-03-10' + INTERVAL '{i % (NUM_DAYS * 24)} hours')"
            for i in range(offset, min(offset + batch, NUM_MEMORIES))
        )
        await execute_query(
            client,
            session_id,
            f"INSERT INTO bench_memories VALUES {values}",
        )

    # Insert executions
    batch = 250
    for offset in range(0, NUM_EXECUTIONS, batch):
        rows = []
        for i in range(offset, min(offset + batch, NUM_EXECUTIONS)):
            agent = i % NUM_AGENTS
            status = "failed" if i % 5 == 0 else "success"
            err = "'timeout after 30s'" if i % 5 == 0 else "NULL"
            hours = i % (NUM_DAYS * 24)
            rows.append(
                f"({agent}, '{status}', {err}, "
                f"TIMESTAMP '2026-03-10' + INTERVAL '{hours} hours'"
                f" + INTERVAL '2 minutes')"
            )
        values = ", ".join(rows)
        await execute_query(
            client,
            session_id,
            f"INSERT INTO bench_executions VALUES {values}",
        )


async def run(url: str, api_key: str) -> BenchmarkResult:
    result = BenchmarkResult(
        name="Analytical Queries",
        description=(
            f"5 analytical queries over {NUM_MEMORIES:,} memories "
            f"and {NUM_EXECUTIONS:,} executions across {NUM_AGENTS} agents."
        ),
    )
    async with make_client(url, api_key) as client:
        session_id = await create_session(client)
        await _setup(client, session_id)

        # Append a unique comment per run to bypass the server-side
        # read-query cache (cache key is SQL + tenant:version, not session).
        run_id = uuid.uuid4().hex[:8]

        result.table_headers = ["Query", "Description", "Latency", "Rows"]
        for qid, desc, sql in QUERIES:
            uncached_sql = f"{sql} -- bench:{run_id}"
            t0 = time.perf_counter()
            r = await execute_query(client, session_id, uncached_sql)
            latency_ms = (time.perf_counter() - t0) * 1000
            rows = r.get("rowcount", len(r.get("rows", [])))
            result.table_rows.append(
                [qid, desc, fmt_ms(latency_ms), str(rows)]
            )

        # Cleanup
        await execute_query(client, session_id, "DROP TABLE bench_memories")
        await execute_query(client, session_id, "DROP TABLE bench_executions")
        await destroy_session(client, session_id)

    result.notes = (
        "These queries are impossible with Mem0 (data split across "
        "Qdrant + Neo4j + SQLite) or Zep (data in Neo4j, no analytical engine). "
        "PondDB runs them in-process on DuckDB with zero data movement."
    )
    return result


async def main() -> None:
    args = build_parser("Benchmark: analytical queries").parse_args()
    r = await run(args.url, args.api_key)
    print(r.to_markdown())


if __name__ == "__main__":
    asyncio.run(main())
