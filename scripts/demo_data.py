#!/usr/bin/env python3
"""Generate demo CSV datasets for PondDB.

Creates three reproducible CSVs in /tmp/ponddb_demo/:
  - sales.csv   (1,000 rows) — date, region, product, quantity, revenue, customer_id
  - users.csv   (200 rows)   — user_id, name, email, signup_date, plan, region
  - events.csv  (5,000 rows) — event_id, user_id, event_type, timestamp, metadata_json

Usage:
    python scripts/demo_data.py [--output-dir /tmp/ponddb_demo]
"""

import argparse
import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# Fixed seed for reproducibility
SEED = 42

REGIONS = ["us-east", "us-west", "eu-west", "eu-central", "ap-southeast"]
PRODUCTS = ["PondDB Pro", "PondDB Team", "PondDB Enterprise", "PondDB Starter", "PondDB Free"]
PLANS = ["free", "pro", "enterprise"]
EVENT_TYPES = ["page_view", "click", "purchase", "signup", "api_call", "query_run"]
FIRST_NAMES = [
    "Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi",
    "Ivan", "Judy", "Karl", "Laura", "Mike", "Nina", "Oscar", "Pam",
    "Quinn", "Ruth", "Sam", "Tina",
]
LAST_NAMES = [
    "Anderson", "Brown", "Chen", "Davis", "Evans", "Foster", "Garcia",
    "Harris", "Ito", "Johnson", "Kim", "Lee", "Martin", "Nelson",
    "O'Brien", "Park", "Quinn", "Rivera", "Smith", "Taylor",
]

# ANSI colors
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def generate_sales(output_dir: Path, rng: random.Random) -> Path:
    """Generate sales.csv with 1,000 rows."""
    path = output_dir / "sales.csv"
    start_date = datetime(2025, 1, 1)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "region", "product", "quantity", "revenue", "customer_id"])
        for i in range(1000):
            date = start_date + timedelta(days=rng.randint(0, 365))
            region = rng.choice(REGIONS)
            product = rng.choice(PRODUCTS)
            qty = rng.randint(1, 50)
            revenue = round(qty * rng.uniform(10.0, 500.0), 2)
            customer_id = f"C{rng.randint(1000, 9999)}"
            writer.writerow([date.strftime("%Y-%m-%d"), region, product, qty, revenue, customer_id])

    return path


def generate_users(output_dir: Path, rng: random.Random) -> Path:
    """Generate users.csv with 200 rows."""
    path = output_dir / "users.csv"
    start_date = datetime(2024, 6, 1)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "name", "email", "signup_date", "plan", "region"])
        for i in range(200):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            name = f"{first} {last}"
            email = f"{first.lower()}.{last.lower()}{i}@example.com"
            signup = start_date + timedelta(days=rng.randint(0, 600))
            plan = rng.choice(PLANS)
            region = rng.choice(REGIONS)
            writer.writerow([f"U{i + 1:04d}", name, email, signup.strftime("%Y-%m-%d"), plan, region])

    return path


def generate_events(output_dir: Path, rng: random.Random) -> Path:
    """Generate events.csv with 5,000 rows."""
    path = output_dir / "events.csv"
    start_ts = datetime(2025, 6, 1)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["event_id", "user_id", "event_type", "timestamp", "metadata_json"])
        for i in range(5000):
            user_id = f"U{rng.randint(1, 200):04d}"
            event_type = rng.choice(EVENT_TYPES)
            ts = start_ts + timedelta(
                days=rng.randint(0, 90),
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            metadata = json.dumps({
                "page": f"/page/{rng.randint(1, 50)}",
                "duration_ms": rng.randint(100, 30000),
                "source": rng.choice(["web", "mobile", "api"]),
            })
            writer.writerow([
                f"E{i + 1:06d}",
                user_id,
                event_type,
                ts.strftime("%Y-%m-%dT%H:%M:%S"),
                metadata,
            ])

    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PondDB demo datasets")
    parser.add_argument(
        "--output-dir",
        default="/tmp/ponddb_demo",
        help="Directory to write CSVs (default: /tmp/ponddb_demo)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)

    print(f"\n{BOLD}{CYAN}PondDB Demo Data Generator{RESET}\n")

    sales_path = generate_sales(output_dir, rng)
    sales_size = os.path.getsize(sales_path)
    print(f"  {GREEN}✓{RESET} sales.csv    — 1,000 rows  ({sales_size:,} bytes)")

    users_path = generate_users(output_dir, rng)
    users_size = os.path.getsize(users_path)
    print(f"  {GREEN}✓{RESET} users.csv    — 200 rows    ({users_size:,} bytes)")

    events_path = generate_events(output_dir, rng)
    events_size = os.path.getsize(events_path)
    print(f"  {GREEN}✓{RESET} events.csv   — 5,000 rows  ({events_size:,} bytes)")

    total = sales_size + users_size + events_size
    print(f"\n  {BOLD}Output:{RESET} {output_dir}")
    print(f"  {BOLD}Total:{RESET}  {total:,} bytes ({total / 1024:.1f} KB)\n")


if __name__ == "__main__":
    main()
