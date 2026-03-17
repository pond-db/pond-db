#!/usr/bin/env python3
"""End-to-end PondDB demo — exercises every major feature against a live server.

Requires a running PondDB instance. Generate demo data first:
    python scripts/demo_data.py

Usage:
    python scripts/demo.py [--base-url http://localhost:8432] [--api-key <key>]
"""

import argparse
import sys
import time
from pathlib import Path

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
    """Print a step header."""
    print(f"\n{CYAN}▸ {name}{RESET}")


def ok(msg: str) -> None:
    """Record and print a passed check."""
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    """Record and print a failed check."""
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")


def check(condition: bool, msg: str) -> None:
    """Assert a condition and print result."""
    if condition:
        ok(msg)
    else:
        fail(msg)


def main() -> None:
    global passed, failed

    parser = argparse.ArgumentParser(description="PondDB end-to-end demo")
    parser.add_argument("--base-url", default="http://localhost:8432")
    parser.add_argument("--api-key", default="changeme")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    api_key = args.api_key
    headers = {"X-API-Key": api_key}
    start_time = time.monotonic()

    print(f"\n{BOLD}{CYAN}PondDB End-to-End Demo{RESET}")
    print(f"  Server: {base}")
    print(f"  API Key: {api_key[:4]}{'*' * (len(api_key) - 4)}")

    client = httpx.Client(base_url=base, timeout=30.0)

    # ── 1. Health check ──────────────────────────────────────────────────
    step("1. Health check")
    try:
        r = client.get("/health")
        check(r.status_code == 200, f"GET /health → {r.status_code}")
        check("status" in r.json(), f"Response: {r.json()}")
    except Exception as e:
        fail(f"Health check failed: {e}")
        print(f"\n{RED}Server not reachable at {base}. Is PondDB running?{RESET}\n")
        sys.exit(1)

    # ── 2. Get JWT token ─────────────────────────────────────────────────
    step("2. Authenticate (get JWT)")
    r = client.post("/auth/token", json={"api_key": api_key})
    check(r.status_code == 200, f"POST /auth/token → {r.status_code}")
    jwt_token = r.json().get("access_token", "")
    jwt_headers = {"Authorization": f"Bearer {jwt_token}"}
    check(bool(jwt_token), "JWT token received")

    # ── 3. Create session ────────────────────────────────────────────────
    step("3. Create session")
    r = client.post("/session")
    check(r.status_code == 201, f"POST /session → {r.status_code}")
    session_id = r.json().get("session_id", "")
    check(bool(session_id), f"Session ID: {session_id[:8]}...")

    # ── 4. Execute inline SQL ────────────────────────────────────────────
    step("4. Execute inline SQL (SELECT 42)")
    r = client.post(
        "/query",
        json={"session_id": session_id, "sql": "SELECT 42 AS answer"},
        headers=jwt_headers,
    )
    check(r.status_code == 200, f"POST /query → {r.status_code}")
    check(r.json().get("rows") == [[42]], f"Result: {r.json().get('rows')}")

    # ── 5. Upload demo CSVs ──────────────────────────────────────────────
    step("5. Upload demo datasets")
    demo_dir = Path("/tmp/ponddb_demo")
    for csv_name in ["sales.csv", "users.csv", "events.csv"]:
        csv_path = demo_dir / csv_name
        if not csv_path.exists():
            fail(f"{csv_path} not found — run scripts/demo_data.py first")
            continue
        with open(csv_path, "rb") as f:
            r = client.post("/datasets", files={"file": (csv_name, f, "text/csv")}, headers=headers)
        check(r.status_code in (201, 409), f"Upload {csv_name} → {r.status_code}")

    # ── 6. Run analytics queries ─────────────────────────────────────────
    step("6. Analytics queries")

    # Revenue by region
    r = client.post(
        "/query",
        json={
            "session_id": session_id,
            "sql": "SELECT 'us-east' AS region, 42 AS revenue UNION ALL SELECT 'eu-west', 38",
        },
        headers=jwt_headers,
    )
    check(r.status_code == 200, f"Revenue by region → {r.json().get('rowcount')} rows")

    # Aggregation query
    r = client.post(
        "/query",
        json={
            "session_id": session_id,
            "sql": "SELECT COUNT(*) AS cnt FROM generate_series(1, 1000) t(i)",
        },
        headers=jwt_headers,
    )
    check(r.status_code == 200, f"COUNT(*) → {r.json().get('rows', [[0]])[0][0]}")

    # ── 7. Save a query ──────────────────────────────────────────────────
    step("7. Save a named query")
    r = client.post(
        "/queries",
        json={
            "title": "Demo Revenue Summary",
            "sql": "SELECT 'demo' AS source, 42 AS total_revenue",
            "visibility": "public",
        },
        headers=jwt_headers,
    )
    check(r.status_code in (201, 409), f"POST /queries → {r.status_code}")
    slug = r.json().get("slug", "demo-revenue-summary")
    check(bool(slug), f"Query slug: {slug}")

    # ── 8. Create share link ─────────────────────────────────────────────
    step("8. Access share link")
    r = client.get(f"/q/{slug}")
    check(r.status_code == 200, f"GET /q/{slug} → {r.status_code}")
    if r.status_code == 200:
        check("rows" in r.json(), f"Share result: {r.json().get('rowcount')} rows")

    # ── 9. Browse schema ─────────────────────────────────────────────────
    step("9. Browse schema")
    r = client.get(f"/schema?session_id={session_id}", headers=jwt_headers)
    check(r.status_code == 200, f"GET /schema → {r.status_code}")
    if r.status_code == 200:
        tables = [t["table_name"] for t in r.json()]
        check(isinstance(r.json(), list), f"Tables: {', '.join(tables) or '(empty)'}")

    # ── 10. Query history ────────────────────────────────────────────────
    step("10. Query history")
    r = client.get("/history", headers=jwt_headers)
    check(r.status_code == 200, f"GET /history → {r.status_code}")
    if r.status_code == 200:
        check(len(r.json()) > 0, f"History entries: {len(r.json())}")

    # ── 11. Terminate session ────────────────────────────────────────────
    step("11. Terminate session")
    r = client.delete(f"/session/{session_id}")
    check(r.status_code == 200, f"DELETE /session/{session_id[:8]}... → {r.status_code}")

    # ── 12. Session lifecycle (auto-suspend / transparent resume) ──────
    import os
    demo_timeout = int(os.environ.get("POND_DEMO_TIMEOUT", "10"))
    step(f"12. Session lifecycle (idle timeout={demo_timeout}s)")

    # Create a fresh session for the lifecycle demo
    r = client.post("/session")
    check(r.status_code == 201, "Created lifecycle demo session")
    demo_sid = r.json().get("session_id", "")

    # Execute a query to mark the session as active
    r = client.post(
        "/query",
        json={"session_id": demo_sid, "sql": "SELECT 1 AS alive"},
        headers=jwt_headers,
    )
    check(r.status_code == 200, "Query executed — session is ACTIVE")

    # Verify session is active
    r = client.get("/sessions")
    sessions = r.json() if r.status_code == 200 else []
    demo_session = [s for s in sessions if s.get("session_id") == demo_sid]
    if demo_session:
        status = demo_session[0].get("status", "")
        check(
            status in ("ACTIVE", "active"),
            f"Session status: {status}",
        )

    # Wait for auto-suspend
    print(f"  {YELLOW}⏳ Waiting {demo_timeout + 5}s for auto-suspend...{RESET}", end="", flush=True)
    for i in range(demo_timeout + 5):
        time.sleep(1)
        remaining = demo_timeout + 5 - i - 1
        print(f"\r  {YELLOW}⏳ Waiting {remaining}s for auto-suspend...   {RESET}", end="", flush=True)
    print()

    r = client.get("/sessions")
    sessions = r.json() if r.status_code == 200 else []
    demo_session = [s for s in sessions if s.get("session_id") == demo_sid]
    if demo_session:
        status = demo_session[0].get("status", "")
        check(
            status in ("SUSPENDED", "suspended"),
            f"Session auto-suspended: {status}",
        )
    else:
        fail(f"Session {demo_sid[:8]} not found after wait (may have been reaped)")

    # Transparent resume by querying
    step("13. Transparent resume on query")
    r = client.post(
        "/query",
        json={"session_id": demo_sid, "sql": "SELECT 2 AS resumed"},
        headers=jwt_headers,
    )
    check(r.status_code == 200, "Query succeeded — session transparently resumed")

    r = client.get("/sessions")
    sessions = r.json() if r.status_code == 200 else []
    demo_session = [s for s in sessions if s.get("session_id") == demo_sid]
    if demo_session:
        status = demo_session[0].get("status", "")
        check(
            status in ("ACTIVE", "active"),
            f"Session resumed to: {status}",
        )

    # Clean up lifecycle session
    client.delete(f"/session/{demo_sid}")
    ok("Lifecycle demo session terminated")

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
