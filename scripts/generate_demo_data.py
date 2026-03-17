"""Generate realistic demo datasets for PondDB examples and demos.

Usage:
    python scripts/generate_demo_data.py

Outputs:
    examples/langgraph-data-analyst/sample_data/sales.csv   (10,000 rows)
    scripts/demo_data/users.csv                              (1,000 rows)
    scripts/demo_data/events.csv                             (50,000 rows)
"""

import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # reproducible output

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Sales data
# ---------------------------------------------------------------------------


def generate_sales_csv(
    path: str = "examples/langgraph-data-analyst/sample_data/sales.csv",
    rows: int = 10_000,
) -> None:
    """Generate realistic sales data with intentional anomalies for demo queries."""
    regions = ["West", "East", "Central", "South", "International"]
    products = ["Pro", "Starter", "Enterprise", "Student", "Free Trial"]
    revenue_ranges = {
        "Enterprise": (500, 5000),
        "Pro": (100, 1500),
        "Starter": (50, 500),
        "Student": (10, 100),
        "Free Trial": (0, 0),
    }

    start_date = datetime(2024, 1, 1)
    end_date = datetime(2025, 12, 31)
    delta_days = (end_date - start_date).days

    out = REPO_ROOT / path
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "region", "product", "revenue", "quantity"])
        for _ in range(rows):
            date = start_date + timedelta(days=random.randint(0, delta_days))
            region = random.choice(regions)
            product = random.choice(products)
            low, high = revenue_ranges[product]
            revenue = round(random.uniform(low, high), 2) if high > 0 else 0.0
            quantity = random.randint(1, 100)

            # Anomaly: March 2025 West region has 3x revenue spike
            if date.month == 3 and date.year == 2025 and region == "West":
                revenue = round(revenue * 3, 2)

            writer.writerow([date.strftime("%Y-%m-%d"), region, product, revenue, quantity])

    print(f"Generated {rows} rows → {out}")


# ---------------------------------------------------------------------------
# Users data
# ---------------------------------------------------------------------------


def generate_users_csv(
    path: str = "scripts/demo_data/users.csv",
    rows: int = 1_000,
) -> None:
    """Generate user account data with plan distribution."""
    plans = ["free", "starter", "pro", "enterprise"]
    plan_weights = [0.50, 0.25, 0.18, 0.07]

    first_names = [
        "Alice", "Bob", "Carol", "David", "Elena", "Frank", "Grace", "Henry",
        "Iris", "James", "Karen", "Leo", "Maya", "Noah", "Olivia", "Peter",
        "Quinn", "Rachel", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xander",
        "Yasmine", "Zoe",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
        "White", "Harris", "Martin", "Thompson", "Lee", "Walker",
    ]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "company.io", "example.com"]

    start_date = datetime(2023, 1, 1)
    end_date = datetime(2025, 12, 31)
    delta_days = (end_date - start_date).days

    out = REPO_ROOT / path
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "email", "signup_date", "plan", "active"])
        for i in range(1, rows + 1):
            first = random.choice(first_names)
            last = random.choice(last_names)
            name = f"{first} {last}"
            email = f"{first.lower()}.{last.lower()}{i}@{random.choice(domains)}"
            signup_date = start_date + timedelta(days=random.randint(0, delta_days))
            plan = random.choices(plans, weights=plan_weights, k=1)[0]
            # enterprise users churn less
            active = random.random() > (0.05 if plan == "enterprise" else 0.20)
            writer.writerow([i, name, email, signup_date.strftime("%Y-%m-%d"), plan, active])

    print(f"Generated {rows} rows → {out}")


# ---------------------------------------------------------------------------
# Events / clickstream data
# ---------------------------------------------------------------------------


def generate_events_csv(
    path: str = "scripts/demo_data/events.csv",
    rows: int = 50_000,
) -> None:
    """Generate product event stream data for funnel / retention analysis."""
    event_types = ["page_view", "signup", "purchase", "logout", "error"]
    event_weights = [0.50, 0.10, 0.15, 0.15, 0.10]
    pages = ["/dashboard", "/query", "/datasets", "/settings", "/pricing", "/docs"]

    start_ts = datetime(2024, 1, 1)
    end_ts = datetime(2025, 12, 31)
    delta_secs = int((end_ts - start_ts).total_seconds())

    out = REPO_ROOT / path
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "user_id", "event_type", "timestamp", "page", "metadata"])
        for i in range(1, rows + 1):
            user_id = random.randint(1, 1_000)
            event_type = random.choices(event_types, weights=event_weights, k=1)[0]
            ts = start_ts + timedelta(seconds=random.randint(0, delta_secs))
            page = random.choice(pages)
            metadata: dict = {}
            if event_type == "purchase":
                metadata = {"amount": round(random.uniform(10, 5000), 2), "currency": "USD"}
            elif event_type == "error":
                metadata = {"code": random.choice([400, 401, 403, 404, 500]), "path": page}
            writer.writerow([
                i, user_id, event_type,
                ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                page,
                json.dumps(metadata),
            ])

    print(f"Generated {rows} rows → {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    generate_sales_csv()
    generate_users_csv()
    generate_events_csv()
    print("Done!")
