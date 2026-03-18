# Multi-Agent Team Demo

Three AI agents collaborate through PondDB's shared memory:

1. **Researcher** discovers facts → stores as shared memories
2. **Analyst** reads findings → adds churn analysis with causal link
3. **Writer** (different team) accesses research via cross-team grant → drafts email

## What this demonstrates

- **Workgroup isolation**: Writer can't see research until a grant is created
- **Selective sharing**: Grant filters by memory type + importance threshold
- **Causal chains**: Analyst's memory links to researcher's via causal_parent_id
- **Monitoring**: Every operation logged in memory_access_log, queryable with SQL
- **Utility scoring**: Memories that lead to good outcomes rank higher

## Run it

```bash
# Start PondDB
docker compose up -d

# Run the demo
python multi_agent_demo.py
```

## Expected output

```
📚 Researcher: storing findings...
📊 Analyst: found 1 research findings
✍️  Writer: received 2 memories via cross-team grant

🔍 Monitoring: What happened during this session?

Total memories created: 4
Cross-team memory access audit:
  writer | search | research-wg-id | 1
Causal chain (research → analysis → email):
  researcher | "Top 3 customers..." | null
  analyst | null | "Churn risk score: Acme=HIGH..."
Memory utility leaderboard:
  researcher | shared | 1 | 0.5 | 0.95
  analyst | shared | 1 | 0.5 | 0.9
  ...

✅ Demo complete.
```

## What makes this different from Mem0

With Mem0, you store and search memories. With PondDB, you also get:

```sql
-- "What did the writer receive from the research team?"
SELECT * FROM memory_access_log WHERE grant_id IS NOT NULL;

-- "Trace the causal chain from research → email"
WITH RECURSIVE chain AS (...) SELECT * FROM chain;

-- "Which memories are frequently accessed but low quality?"
SELECT * FROM agent_memories WHERE utility < 0.3 AND access_count > 10;
```

These queries are impossible with Mem0 because their data is split across 3 databases
(Qdrant + Neo4j + SQLite). PondDB keeps everything in one database — memories, access
logs, and execution history are all JOINable.
