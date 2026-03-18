<p align="center">
  <img src="static/ponddb-logo-wordmark.svg" alt="PondDB" height="50">
</p>

<p align="center">
  <strong>The open-source memory database for AI agent teams.</strong><br>
  Store, share, and debug agent memories with SQL.
</p>

<p align="center">
  <a href="https://github.com/pond-db/pond-db/actions/workflows/ci.yml"><img src="https://github.com/pond-db/pond-db/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="#"><img src="https://img.shields.io/badge/tests-1%2C223%20passing-brightgreen" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/license-BSL%201.1-blue" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python"></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#why-ponddb">Why PondDB</a> ·
  <a href="#agent-memory-in-60-seconds">Agent Memory in 60 Seconds</a> ·
  <a href="#multi-agent-demo">Multi-Agent Demo</a> ·
  <a href="#use-with-claude-code">Use with Claude Code</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#contributing">Contributing</a>
</p>

---

## Quickstart

```bash
git clone https://github.com/pond-db/pond-db && cd pond-db
cp .env.example .env
docker compose up -d
# PondDB running at http://localhost:8432
```

## Why PondDB

Multi-agent systems today stitch together Redis + Postgres + Pinecone for agent memory. When something goes wrong, you can't answer: **"What did the agent know when it made that decision?"**

PondDB is one self-hosted database for agent memory. Every read, write, and search is logged. Debug your agents with SQL, not guesswork.

```sql
-- The query no other memory system can run:
-- "What did my agent access right before it failed?"
SELECT m.agent_id, m.content, m.utility, l.action, l.created_at
FROM memory_access_log l
JOIN agent_memories m ON m.id IN (SELECT value FROM json_each(l.memory_ids))
WHERE l.status = 'error'
ORDER BY l.created_at DESC;
```

### How PondDB compares

|  | Mem0 | Zep | AWS AgentCore | PondDB |
|--|------|-----|---------------|--------|
| Databases needed | 3 (Qdrant + Neo4j + SQLite) | 2 (Neo4j + Postgres) | Managed (black box) | **1** |
| Multi-agent shared memory | No | No | Namespaces | **Workgroups + grants** |
| SQL analytics on agent behavior | No | No | No | **Yes (DuckDB)** |
| Complete audit trail | No | No | Limited | **Every operation logged** |
| Self-hosted | Yes | No | No | **Yes (Docker Compose)** |
| Cross-team sharing with access control | No | No | IAM policies | **Grants with type + importance filters** |

## Agent Memory in 60 Seconds

**Store a memory:**

```python
import httpx

client = httpx.Client(
    base_url="http://localhost:8432",
    headers={"Authorization": "Bearer YOUR_API_KEY"}
)

# Agent stores what it learned
client.post("/memories", json={
    "agent_id": "researcher",
    "memory_type": "semantic",
    "content": {"fact": "Acme Corp revenue is $2.1M, renewing Q2"},
    "access_scope": "workgroup",
    "importance": 0.9
})
```

**Search memories:**

```python
# Another agent in the same team finds it
memories = client.get("/memories/search", params={
    "memory_type": "semantic",
    "min_importance": 0.7
}).json()

for m in memories:
    print(f"[{m['agent_id']}] {m['content']}")
# Output: [researcher] {"fact": "Acme Corp revenue is $2.1M, renewing Q2"}
```

**Rate a memory's usefulness:**

```python
# Agent reports: this memory helped me succeed
client.post(f"/memories/{memories[0]['id']}/feedback", json={
    "reward": 0.9  # positive = useful, negative = misleading
})
# Utility score increases — useful memories rank higher next time
```

**Debug with SQL:**

```python
# Which memories are accessed most but have low utility? (bad memories polluting context)
result = client.post("/pondapi/execute", json={
    "sql": """
        SELECT agent_id, content, utility, access_count
        FROM agent_memories
        WHERE utility < 0.3 AND access_count > 10
        ORDER BY access_count DESC
    """
}).json()
```

## Multi-Agent Demo

A complete working example: 3 agents (researcher, analyst, writer) collaborate through shared memory with cross-team access grants.

```python
"""
PondDB Multi-Agent Team Demo

3 agents across 2 workgroups share memories through grants.
Run: python examples/multi-agent-team/multi_agent_demo.py
Requires: PondDB running at http://localhost:8432
"""
import httpx, json, time

BASE = "http://localhost:8432"

# ── Setup: Create workgroups and agents ──────────────────────────
admin = httpx.Client(base_url=BASE)
token = admin.post("/auth/token", data={
    "username": "admin", "password": "admin"  # from .env
}).json()["access_token"]
h = {"Authorization": f"Bearer {token}"}

# Two teams: research and content
research_wg = admin.post("/workgroups", json={"name": "research"}, headers=h).json()
content_wg = admin.post("/workgroups", json={"name": "content"}, headers=h).json()

# API keys for each agent
key_researcher = admin.post("/api-keys", json={
    "workgroup_id": research_wg["id"], "name": "researcher"
}, headers=h).json()["key"]

key_analyst = admin.post("/api-keys", json={
    "workgroup_id": research_wg["id"], "name": "analyst"
}, headers=h).json()["key"]

key_writer = admin.post("/api-keys", json={
    "workgroup_id": content_wg["id"], "name": "writer"
}, headers=h).json()["key"]

# Grant: content team can READ research team's shared memories
admin.post("/memory-grants", json={
    "grantor_workgroup_id": research_wg["id"],
    "grantee_workgroup_id": content_wg["id"],
    "memory_type_filter": "shared",
    "permission": "read",
    "min_importance": 0.7
}, headers=h)

# ── Phase 1: Researcher discovers facts ──────────────────────────
researcher = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_researcher}"})

print("📚 Researcher: storing findings...")
researcher.post("/memories", json={
    "agent_id": "researcher",
    "memory_type": "shared",
    "content": {
        "finding": "Top 3 customers by revenue: Acme ($500K), Beta ($350K), Gamma ($200K)",
        "insight": "Acme evaluating competitors — highest churn risk",
        "source": "CRM data + sales call notes"
    },
    "access_scope": "workgroup",
    "importance": 0.95
})

researcher.post("/memories", json={
    "agent_id": "researcher",
    "memory_type": "procedural",
    "content": {"lesson": "Always check renewal date before outreach — prevents awkward timing"},
    "access_scope": "workgroup",
    "importance": 0.7
})

# ── Phase 2: Analyst builds on researcher's work ────────────────
analyst = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_analyst}"})

# Analyst sees researcher's memories (same workgroup)
findings = analyst.get("/memories/search", params={
    "memory_type": "shared", "min_importance": 0.8
}).json()

print(f"📊 Analyst: found {len(findings)} research findings")

analyst.post("/memories", json={
    "agent_id": "analyst",
    "memory_type": "shared",
    "content": {
        "analysis": "Churn risk score: Acme=HIGH (competitor eval), Beta=LOW (just renewed), Gamma=MEDIUM (usage declining)",
        "recommendation": "Prioritize Acme retention outreach immediately"
    },
    "access_scope": "workgroup",
    "importance": 0.9,
    "causal_parent_id": findings[0]["id"]  # links to researcher's finding
})

# ── Phase 3: Writer accesses research via grant ─────────────────
writer = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_writer}"})

# Writer is in content team but can see research team's shared memories
research_memories = writer.get("/memories/search", params={
    "memory_type": "shared", "min_importance": 0.7
}).json()

print(f"✍️  Writer: received {len(research_memories)} memories via cross-team grant")

writer.post("/memories", json={
    "agent_id": "writer",
    "memory_type": "episodic",
    "content": {
        "draft": "Dear Acme team, we noticed your contract renews next quarter...",
        "customer": "Acme",
        "based_on": "researcher findings + analyst churn score"
    },
    "access_scope": "workgroup",
    "importance": 0.8
})

# ── Phase 4: The monitoring queries PondDB enables ──────────────
print("\n🔍 Monitoring: What happened during this session?\n")

# Query 1: Complete audit trail
logs = admin.get("/memories/search", params={"limit": 100}, headers=h).json()
print(f"Total memories created: {len(logs)}")

# Query 2: Cross-team access audit
print("\nCross-team memory access audit:")
audit = admin.post("/pondapi/execute", json={
    "sql": """
        SELECT agent_id, action, source_workgroup_id, COUNT(*) as accesses
        FROM memory_access_log
        WHERE grant_id IS NOT NULL
        GROUP BY 1, 2, 3
    """
}, headers=h).json()
for row in audit.get("rows", []):
    print(f"  {row}")

# Query 3: Causal chain — how did we get from research to email?
print("\nCausal chain (research → analysis → email):")
chain = admin.post("/pondapi/execute", json={
    "sql": """
        WITH RECURSIVE chain AS (
            SELECT id, agent_id, content, causal_parent_id, 0 as depth
            FROM agent_memories WHERE agent_id = 'analyst'
            UNION ALL
            SELECT m.id, m.agent_id, m.content, m.causal_parent_id, c.depth + 1
            FROM agent_memories m JOIN chain c ON m.id = c.causal_parent_id
            WHERE c.depth < 10
        )
        SELECT agent_id, json_extract(content, '$.finding') as finding,
               json_extract(content, '$.analysis') as analysis
        FROM chain ORDER BY depth DESC
    """
}, headers=h).json()
for row in chain.get("rows", []):
    print(f"  {row}")

# Query 4: Memory utility leaderboard
print("\nMemory utility leaderboard:")
leaderboard = admin.post("/pondapi/execute", json={
    "sql": """
        SELECT agent_id, memory_type, COUNT(*) as memories,
               ROUND(AVG(utility), 2) as avg_utility,
               ROUND(AVG(importance), 2) as avg_importance
        FROM agent_memories
        GROUP BY agent_id, memory_type
        ORDER BY avg_utility DESC
    """
}, headers=h).json()
for row in leaderboard.get("rows", []):
    print(f"  {row}")

print("\n✅ Demo complete. Every operation above was logged in memory_access_log.")
print("   Query it with SQL to see exactly what each agent knew and when.")
```

### What makes this different from Mem0

Mem0 stores memories. PondDB stores memories **AND shows you how agents use them**:

| After this demo, you can answer... | Mem0 | PondDB |
|-------------------------------------|------|--------|
| "What did the writer receive from the research team?" | ❌ | `SELECT * FROM memory_access_log WHERE grant_id IS NOT NULL` |
| "What was the causal chain from research to email?" | ❌ | `WITH RECURSIVE chain AS (...)` |
| "Which memories have low utility but high access count?" | ❌ | `SELECT * WHERE utility < 0.3 AND access_count > 10` |
| "Show me every cross-team memory access this week" | ❌ | `SELECT * FROM memory_access_log WHERE grant_id IS NOT NULL` |

## Memory Types

| Type | Purpose | Lifetime | Example |
|------|---------|----------|---------|
| `working` | Current task context | Auto-expires (configurable) | "Currently analyzing Q1 data" |
| `episodic` | What happened | Permanent | "Drafted email to Acme on March 18" |
| `semantic` | Extracted facts | Permanent | "Acme revenue is $500K" |
| `procedural` | Learned patterns | Permanent | "Check renewal date before outreach" |
| `shared` | Cross-agent team state | Permanent | "Acme is highest churn risk" |

## Multi-Agent Isolation

Agents are isolated by workgroup. Cross-workgroup sharing requires explicit grants:

```bash
# Grant content-team READ access to research-team's shared memories
POST /memory-grants
{
    "grantor_workgroup_id": "research-team-uuid",
    "grantee_workgroup_id": "content-team-uuid",
    "memory_type_filter": "shared",       # only shared memories
    "min_importance": 0.7,                 # only high-importance
    "permission": "read",                  # read-only
    "valid_until": "2026-04-01T00:00:00Z"  # expires April 1
}
```

Tested: **0 cross-workgroup leaks** across 3,000+ isolation queries.

## Use with Claude Code (MCP)

```bash
pip install mcp-server-ponddb
```

Add to your Claude Code config (`~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "ponddb": {
      "command": "python",
      "args": ["-m", "mcp_server_ponddb"],
      "env": {
        "PONDDB_URL": "http://localhost:8432",
        "PONDDB_API_KEY": "your-api-key"
      }
    }
  }
}
```

Then in Claude Code:

> *"Remember that Acme's contract renews in Q2 and they're evaluating competitors"*
>
> *"What do you remember about Acme?"*
>
> *"Forget the memory about Acme's competitors"*

## Utility Scoring

Memories that help agents succeed rank higher over time. When an agent uses a memory and the task succeeds, call the feedback endpoint:

```
POST /memories/{id}/feedback {"reward": 0.9}   # positive = memory was useful
```

PondDB updates the utility score using a reinforcement learning formula (inspired by [MemRL](https://arxiv.org/abs/2312.12345)). Over time, good memories float to the top and bad ones sink.

## OpenTelemetry Tracing

PondDB emits OTel spans for every memory operation. Route them to Langfuse, Datadog, or Jaeger:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-langfuse:4318 docker compose up -d
```

Every span includes: `ponddb.agent_id`, `ponddb.memory_type`, `ponddb.cross_workgroup`, `ponddb.utility`.

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/memories` | POST | Store a memory |
| `/memories/search` | GET | Search memories (grant-aware) |
| `/memories/{id}` | GET | Get single memory |
| `/memories/{id}` | PUT | Update a memory |
| `/memories/{id}` | DELETE | Soft-delete a memory |
| `/memories/{id}/feedback` | POST | Update utility score |
| `/memory-grants` | POST | Create cross-workgroup grant |
| `/memory-grants/{id}` | DELETE | Revoke a grant |
| `/pondapi/execute` | POST | Run SQL queries |

Full API docs at `http://localhost:8432/docs` after starting PondDB.

## Benchmarks

| Metric | Result |
|--------|--------|
| Memory search (10K memories) | 0.9ms p50 |
| Cross-workgroup isolation | **0 leaks** / 3K+ queries |
| Memory write throughput | 29 writes/sec (SQLite*) |
| Concurrent write (10 agents) | 47 writes/sec (SQLite*) |
| DuckDB analytical queries | 1.3ms – 44.6ms |
| PondAPI concurrent write (DuckDB) | 903 writes/sec |
| Read under write load (DuckDB) | 2.7ms p50 |

\* *Memory writes go through SQLite (single-node OLTP). Migration to Postgres is planned for Phase 10, targeting 500+ writes/sec.*

[Full benchmark results](./benchmarks/RESULTS.md)

## Architecture

PondDB uses a dual-engine architecture:

- **SQLite** — Agent memory CRUD (fast single-node writes, will migrate to Postgres)
- **DuckDB** — Analytical queries across agent behavior (the queries Mem0 can't run)
- **FastAPI** — HTTP API with JWT auth, rate limiting, CORS
- **OpenTelemetry** — Trace emission for external monitoring tools

## Examples

| Example | Description |
|---------|-------------|
| [Quickstart](./examples/quickstart/) | Memory CRUD in 20 lines |
| [Claude Code MCP](./examples/claude-code-mcp/) | Use PondDB as Claude Code's memory |
| [Multi-Agent Team](./examples/multi-agent-team/) | 3 agents with cross-team grants |

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).

## License

[BSL 1.1](./LICENSE) — free for internal use, requires license for hosting as a service.

<p align="center">
Built on <a href="https://duckdb.org">DuckDB</a> · Created by <a href="https://github.com/houtianlu">Tianlu</a>
</p>
