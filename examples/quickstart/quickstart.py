#!/usr/bin/env python3
"""PondDB Quickstart — Agent Memory in 20 Lines

Prerequisites:
  pip install httpx
  PondDB running: docker compose up -d
"""

import httpx

# Connect to PondDB
client = httpx.Client(
    base_url="http://localhost:8432",
    headers={"Authorization": "Bearer admin-api-key"},  # from .env
)

# 1. Store a memory
memory = client.post(
    "/memories",
    json={
        "agent_id": "my-agent",
        "memory_type": "semantic",
        "content": {"fact": "User prefers dark mode and concise answers"},
        "importance": 0.8,
    },
).json()
print(f"Stored: {memory['id']}")

# 2. Search memories
results = client.get(
    "/memories/search",
    params={
        "memory_type": "semantic",
        "min_importance": 0.5,
    },
).json()
print(f"Found {len(results)} memories")

# 3. Rate it
client.post(f"/memories/{memory['id']}/feedback", json={"reward": 0.9})
print("Utility updated — useful memories rank higher next time")

# 4. Debug: what did the agent access?
logs = client.post(
    "/pondapi/execute",
    json={
        "sql": (
            "SELECT agent_id, action, created_at "
            "FROM memory_access_log ORDER BY created_at DESC LIMIT 5"
        ),
    },
).json()
print(f"Access log: {logs.get('rows', [])}")
