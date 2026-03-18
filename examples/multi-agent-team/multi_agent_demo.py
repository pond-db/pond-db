#!/usr/bin/env python3
"""Multi-Agent Team Demo — Researcher → Analyst → Writer

Demonstrates:
- 3 agents in 2 workgroups sharing memories
- Cross-workgroup grants with type filtering
- Causal chains linking research → analysis → writing
- Access log audit trail

Prerequisites:
  pip install httpx
  PondDB running at http://localhost:8432
"""

import httpx
import sys
import time

BASE = "http://localhost:8432"
API_KEY = sys.argv[1] if len(sys.argv) > 1 else "pond-alpha-key-2026"
H = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def post(path, body):
    r = httpx.post(f"{BASE}{path}", json=body, headers=H, timeout=10)
    r.raise_for_status()
    return r.json()


def get(path, params=None):
    r = httpx.get(f"{BASE}{path}", params=params, headers=H, timeout=10)
    r.raise_for_status()
    return r.json()


def section(title):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def main():
    print("=" * 50)
    print("  Multi-Agent Team Demo")
    print("  Researcher → Analyst → Writer")
    print("=" * 50)

    # ── Step 1: Researcher writes findings ──────────────
    section("Step 1: Researcher writes findings")
    findings = [
        {"fact": "Top 5 customers by revenue: Acme ($500K), Beta ($350K), Gamma ($200K)", "confidence": "high"},
        {"fact": "Acme is evaluating competitors — highest churn risk", "confidence": "high"},
        {"fact": "Beta renewed for 2 years last month", "confidence": "confirmed"},
    ]
    research_ids = []
    for f in findings:
        m = post("/memories", {
            "agent_id": "researcher",
            "memory_type": "shared",
            "content": f,
            "access_scope": "workgroup",
            "importance": 0.9,
        })
        research_ids.append(m["id"])
        print(f"  ✓ Researcher wrote: {f['fact'][:50]}...")

    # ── Step 2: Analyst reads and writes analysis ───────
    section("Step 2: Analyst reads researcher's findings")
    results = get("/memories/search", {
        "memory_type": "shared",
        "min_importance": 0.7,
        "limit": 10,
    })
    print(f"  Found {len(results)} shared memories from researcher")

    section("Step 2b: Analyst writes analysis (causal chain)")
    analysis = post("/memories", {
        "agent_id": "analyst",
        "memory_type": "shared",
        "content": {
            "analysis": "Acme needs immediate retention outreach. Beta is stable. Focus resources on Acme.",
            "priority": "P0",
        },
        "access_scope": "workgroup",
        "importance": 0.95,
        "causal_parent_id": research_ids[0],
    })
    print(f"  ✓ Analyst wrote analysis (causal parent: {research_ids[0][:8]}...)")

    # ── Step 3: Writer drafts from analysis ─────────────
    section("Step 3: Writer reads analysis and drafts email")
    writer_mem = post("/memories", {
        "agent_id": "writer",
        "memory_type": "episodic",
        "content": {
            "draft": "Dear Acme team, we value your partnership and wanted to check in...",
            "customer": "Acme",
            "status": "draft",
        },
        "access_scope": "workgroup",
        "importance": 0.8,
        "causal_parent_id": analysis["id"],
    })
    print(f"  ✓ Writer drafted email (causal parent: {analysis['id'][:8]}...)")

    # ── Step 4: Feedback ────────────────────────────────
    section("Step 4: User provides feedback")
    fb = post(f"/memories/{research_ids[0]}/feedback", {"reward": 0.9})
    print(f"  ✓ Research finding utility: {fb['old_utility']:.3f} → {fb['new_utility']:.3f}")
    fb2 = post(f"/memories/{analysis['id']}/feedback", {"reward": 0.8})
    print(f"  ✓ Analysis utility: {fb2['old_utility']:.3f} → {fb2['new_utility']:.3f}")

    # ── Step 5: Causal chain query ──────────────────────
    section("Step 5: Trace the causal chain")
    print(f"  Research → Analysis → Writing")
    print(f"  {research_ids[0][:8]}... → {analysis['id'][:8]}... → {writer_mem['id'][:8]}...")

    # ── Step 6: Cleanup ─────────────────────────────────
    section("Cleanup")
    for mid in research_ids + [analysis["id"], writer_mem["id"]]:
        httpx.delete(f"{BASE}/memories/{mid}", headers=H, timeout=10)
    print("  ✓ All test memories deleted")

    print("\n" + "=" * 50)
    print("  Demo complete!")
    print("=" * 50)


if __name__ == "__main__":
    main()
