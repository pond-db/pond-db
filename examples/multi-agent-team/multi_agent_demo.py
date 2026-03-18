#!/usr/bin/env python3
"""PondDB Multi-Agent Team Demo

3 agents across 2 workgroups share memories through grants.
Run: python examples/multi-agent-team/multi_agent_demo.py
Requires: PondDB running at http://localhost:8432
"""

import httpx

BASE = "http://localhost:8432"

# ── Setup: Create workgroups and agents ──────────────────────────
admin = httpx.Client(base_url=BASE)
token = admin.post(
    "/auth/token",
    data={
        "username": "admin",
        "password": "admin",  # from .env
    },
).json()["access_token"]
h = {"Authorization": f"Bearer {token}"}

# Two teams: research and content
research_wg = admin.post("/workgroups", json={"name": "research"}, headers=h).json()
content_wg = admin.post("/workgroups", json={"name": "content"}, headers=h).json()

# API keys for each agent
key_researcher = admin.post(
    "/api-keys", json={"workgroup_id": research_wg["id"], "name": "researcher"}, headers=h
).json()["key"]

key_analyst = admin.post(
    "/api-keys", json={"workgroup_id": research_wg["id"], "name": "analyst"}, headers=h
).json()["key"]

key_writer = admin.post(
    "/api-keys", json={"workgroup_id": content_wg["id"], "name": "writer"}, headers=h
).json()["key"]

# Grant: content team can READ research team's shared memories
admin.post(
    "/memory-grants",
    json={
        "grantor_workgroup_id": research_wg["id"],
        "grantee_workgroup_id": content_wg["id"],
        "memory_type_filter": "shared",
        "permission": "read",
        "min_importance": 0.7,
    },
    headers=h,
)

# ── Phase 1: Researcher discovers facts ──────────────────────────
researcher = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_researcher}"})

print("📚 Researcher: storing findings...")
researcher.post(
    "/memories",
    json={
        "agent_id": "researcher",
        "memory_type": "shared",
        "content": {
            "finding": "Top 3 customers by revenue: Acme ($500K), Beta ($350K), Gamma ($200K)",
            "insight": "Acme evaluating competitors — highest churn risk",
            "source": "CRM data + sales call notes",
        },
        "access_scope": "workgroup",
        "importance": 0.95,
    },
)

researcher.post(
    "/memories",
    json={
        "agent_id": "researcher",
        "memory_type": "procedural",
        "content": {
            "lesson": "Always check renewal date before outreach — prevents awkward timing"
        },
        "access_scope": "workgroup",
        "importance": 0.7,
    },
)

# ── Phase 2: Analyst builds on researcher's work ────────────────
analyst = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_analyst}"})

# Analyst sees researcher's memories (same workgroup)
findings = analyst.get(
    "/memories/search", params={"memory_type": "shared", "min_importance": 0.8}
).json()

print(f"📊 Analyst: found {len(findings)} research findings")

analyst.post(
    "/memories",
    json={
        "agent_id": "analyst",
        "memory_type": "shared",
        "content": {
            "analysis": (
                "Churn risk score: Acme=HIGH (competitor eval), "
                "Beta=LOW (just renewed), Gamma=MEDIUM (usage declining)"
            ),
            "recommendation": "Prioritize Acme retention outreach immediately",
        },
        "access_scope": "workgroup",
        "importance": 0.9,
        "causal_parent_id": findings[0]["id"],  # links to researcher's finding
    },
)

# ── Phase 3: Writer accesses research via grant ─────────────────
writer = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key_writer}"})

# Writer is in content team but can see research team's shared memories
research_memories = writer.get(
    "/memories/search", params={"memory_type": "shared", "min_importance": 0.7}
).json()

print(f"✍️  Writer: received {len(research_memories)} memories via cross-team grant")

writer.post(
    "/memories",
    json={
        "agent_id": "writer",
        "memory_type": "episodic",
        "content": {
            "draft": "Dear Acme team, we noticed your contract renews next quarter...",
            "customer": "Acme",
            "based_on": "researcher findings + analyst churn score",
        },
        "access_scope": "workgroup",
        "importance": 0.8,
    },
)

# ── Phase 4: The monitoring queries PondDB enables ──────────────
print("\n🔍 Monitoring: What happened during this session?\n")

# Query 1: Complete audit trail
logs = admin.get("/memories/search", params={"limit": 100}, headers=h).json()
print(f"Total memories created: {len(logs)}")

# Query 2: Cross-team access audit
print("\nCross-team memory access audit:")
audit = admin.post(
    "/pondapi/execute",
    json={
        "sql": """
        SELECT agent_id, action, source_workgroup_id, COUNT(*) as accesses
        FROM memory_access_log
        WHERE grant_id IS NOT NULL
        GROUP BY 1, 2, 3
    """
    },
    headers=h,
).json()
for row in audit.get("rows", []):
    print(f"  {row}")

# Query 3: Causal chain — how did we get from research to email?
print("\nCausal chain (research → analysis → email):")
chain = admin.post(
    "/pondapi/execute",
    json={
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
    },
    headers=h,
).json()
for row in chain.get("rows", []):
    print(f"  {row}")

# Query 4: Memory utility leaderboard
print("\nMemory utility leaderboard:")
leaderboard = admin.post(
    "/pondapi/execute",
    json={
        "sql": """
        SELECT agent_id, memory_type, COUNT(*) as memories,
               ROUND(AVG(utility), 2) as avg_utility,
               ROUND(AVG(importance), 2) as avg_importance
        FROM agent_memories
        GROUP BY agent_id, memory_type
        ORDER BY avg_utility DESC
    """
    },
    headers=h,
).json()
for row in leaderboard.get("rows", []):
    print(f"  {row}")

print("\n✅ Demo complete. Every operation above was logged in memory_access_log.")
print("   Query it with SQL to see exactly what each agent knew and when.")
