#!/usr/bin/env python3
"""PondDB Agent Memory Quickstart

Demonstrates: create memories, search, cross-agent sharing,
utility feedback, and the monitoring query.

Prerequisites:
  pip install httpx
  PondDB running at http://localhost:8432
"""

import httpx
import json
import sys

BASE = "http://localhost:8432"
API_KEY = sys.argv[1] if len(sys.argv) > 1 else "pond-alpha-key-2026"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def post(path, body):
    r = httpx.post(f"{BASE}{path}", json=body, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def get(path, params=None):
    r = httpx.get(f"{BASE}{path}", params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def main():
    print("=" * 60)
    print("  PondDB Agent Memory Quickstart")
    print("=" * 60)

    # 1. Agent 1 writes 5 semantic memories
    print("\n1. Agent 'researcher' writes 5 memories...")
    for i, fact in enumerate([
        "Q1 revenue was $2.1M",
        "Customer Acme is evaluating competitors",
        "Deployment uses Kubernetes with 3 replicas",
        "Database migration scheduled for March 25",
        "New hire starts next Monday in engineering",
    ]):
        r = post("/memories", {
            "agent_id": "researcher",
            "memory_type": "semantic",
            "content": {"fact": fact, "source": "quickstart"},
            "access_scope": "workgroup",
            "importance": 0.7 + i * 0.05,
        })
        print(f"   Created: {r['id'][:8]}... — {fact[:40]}")

    # 2. Agent 2 searches and finds them
    print("\n2. Agent 'analyst' searches for semantic memories...")
    results = get("/memories/search", {
        "memory_type": "semantic",
        "min_importance": 0.7,
        "limit": 10,
    })
    print(f"   Found {len(results)} memories:")
    for m in results:
        print(f"   - [{m['importance']:.2f}] {m['content'].get('fact', '')[:50]}")

    # 3. Provide feedback
    if results:
        mem_id = results[0]["id"]
        old_utility = results[0]["utility"]
        print(f"\n3. Providing positive feedback (reward=0.8) on memory {mem_id[:8]}...")
        fb = post(f"/memories/{mem_id}/feedback", {"reward": 0.8})
        print(f"   Utility: {fb['old_utility']:.3f} → {fb['new_utility']:.3f}")

    # 4. Search again — high-utility memory should rank first
    print("\n4. Searching again — utility-boosted memory ranks higher...")
    results2 = get("/memories/search", {"memory_type": "semantic", "limit": 5})
    for i, m in enumerate(results2):
        marker = " ← boosted" if m.get("utility", 0) > 0.5 else ""
        print(f"   #{i+1} [utility={m['utility']:.3f}] {m['content'].get('fact', '')[:40]}{marker}")

    # 5. Cleanup
    print("\n5. Cleaning up test memories...")
    for m in results2:
        httpx.delete(f"{BASE}/memories/{m['id']}", headers=HEADERS, timeout=10)
    print("   Done!")

    print("\n" + "=" * 60)
    print("  Quickstart complete. See README.md for more examples.")
    print("=" * 60)


if __name__ == "__main__":
    main()
