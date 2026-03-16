#!/usr/bin/env python3
"""Admin operations demo — namespaces, workgroups, invites, quotas.

Requires a running PondDB instance.

Usage:
    python scripts/demo_admin.py [--base-url http://localhost:8432] [--api-key <key>]
"""

import argparse
import sys
import time

import httpx

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed = 0
failed = 0


def step(name: str) -> None:
    print(f"\n{CYAN}▸ {name}{RESET}")


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")


def check(condition: bool, msg: str) -> None:
    if condition:
        ok(msg)
    else:
        fail(msg)


def main() -> None:
    global passed, failed

    parser = argparse.ArgumentParser(description="PondDB admin operations demo")
    parser.add_argument("--base-url", default="http://localhost:8432")
    parser.add_argument("--api-key", default="changeme")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    api_key = args.api_key
    start_time = time.monotonic()

    print(f"\n{BOLD}{CYAN}PondDB Admin Operations Demo{RESET}")
    print(f"  Server: {base}")

    client = httpx.Client(base_url=base, timeout=30.0)

    # ── 1. Get admin JWT ─────────────────────────────────────────────────
    step("1. Authenticate as admin")
    r = client.post("/auth/token", json={"api_key": api_key})
    check(r.status_code == 200, f"POST /auth/token → {r.status_code}")
    token = r.json().get("access_token", "")
    auth = {"Authorization": f"Bearer {token}"}
    check(bool(token), "Admin JWT received")

    # ── 2. Create namespace ──────────────────────────────────────────────
    step("2. Create namespace 'demo-org'")
    r = client.post(
        "/namespaces",
        json={"name": "demo-org", "description": "Demo organization"},
        headers=auth,
    )
    check(r.status_code in (201, 409), f"POST /namespaces → {r.status_code}")
    ns_id = r.json().get("id", "")
    if r.status_code == 409:
        # Already exists — list and find it
        ns_list = client.get("/namespaces", headers=auth).json()
        ns = next((n for n in ns_list if n["name"] == "demo-org"), None)
        if ns:
            ns_id = ns["id"]
            ok("Namespace already exists, reusing")

    check(bool(ns_id), f"Namespace ID: {ns_id[:8]}..." if ns_id else "No namespace ID")

    # ── 3. Create workgroup ──────────────────────────────────────────────
    step("3. Create workgroup 'analytics'")
    r = client.post(
        "/workgroups",
        json={
            "name": "analytics",
            "namespace_id": ns_id,
            "quota": {"max_sessions": 5},
        },
        headers=auth,
    )
    check(r.status_code in (201, 409), f"POST /workgroups → {r.status_code}")
    wg_id = r.json().get("id", "")
    if r.status_code == 409:
        wg_list = client.get("/workgroups", headers=auth).json()
        wg = next((w for w in wg_list if w["name"] == "analytics"), None)
        if wg:
            wg_id = wg["id"]
            ok("Workgroup already exists, reusing")

    check(bool(wg_id), f"Workgroup ID: {wg_id[:8]}..." if wg_id else "No workgroup ID")

    # ── 4. Create invite ─────────────────────────────────────────────────
    step("4. Create invite for demo@example.com")
    r = client.post(
        "/invites",
        json={"email": "demo@example.com", "role": "member", "expires_in_hours": 168},
        headers=auth,
    )
    check(r.status_code == 201, f"POST /invites → {r.status_code}")
    invite_token = r.json().get("token", "")
    check(bool(invite_token), f"Invite token: {invite_token[:12]}..." if invite_token else "None")

    # ── 5. Accept invite (simulated) ─────────────────────────────────────
    step("5. Accept invite (simulated)")
    if invite_token:
        r = client.post(
            f"/invites/{invite_token}/accept",
            json={"email": "demo@example.com"},
        )
        check(r.status_code == 200, f"POST /invites/{invite_token[:8]}../accept → {r.status_code}")
        new_token = r.json().get("access_token", "")
        check(bool(new_token), "New user JWT issued")
    else:
        fail("No invite token to accept")

    # ── 6. Set workgroup quota ───────────────────────────────────────────
    step("6. Update workgroup quota")
    if wg_id:
        r = client.put(
            f"/workgroups/{wg_id}",
            json={"quota": {"max_sessions": 10}},
            headers=auth,
        )
        check(r.status_code == 200, f"PUT /workgroups/{wg_id[:8]}... → {r.status_code}")
        quota = r.json().get("quota", {})
        check(quota.get("max_sessions") == 10, f"Quota updated: max_sessions={quota.get('max_sessions')}")
    else:
        fail("No workgroup to update")

    # ── 7. Check workgroup usage ─────────────────────────────────────────
    step("7. Workgroup usage stats")
    if wg_id:
        r = client.get(f"/workgroups/{wg_id}/usage", headers=auth)
        check(r.status_code == 200, f"GET /workgroups/{wg_id[:8]}../usage → {r.status_code}")
        usage = r.json()
        check("usage" in usage, f"Active sessions: {usage.get('usage', {}).get('active_sessions', 'N/A')}")
    else:
        fail("No workgroup to check")

    # ── 8. List invites ──────────────────────────────────────────────────
    step("8. List invites")
    r = client.get("/invites", headers=auth)
    check(r.status_code == 200, f"GET /invites → {r.status_code}")
    if r.status_code == 200:
        invites = r.json()
        check(len(invites) > 0, f"Invites found: {len(invites)}")

    # ── 9. Revoke invite ─────────────────────────────────────────────────
    step("9. Revoke invite")
    if invite_token:
        r = client.delete(f"/invites/{invite_token}", headers=auth)
        check(r.status_code in (200, 404), f"DELETE /invites/{invite_token[:8]}... → {r.status_code}")
    else:
        fail("No invite to revoke")

    # ── Summary ──────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start_time
    total = passed + failed
    print(f"\n{'─' * 50}")
    print(f"{BOLD}Results:{RESET} {GREEN}{passed} passed{RESET}, {RED}{failed} failed{RESET}, {total} total")
    print(f"{BOLD}Elapsed:{RESET} {elapsed:.1f}s\n")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
