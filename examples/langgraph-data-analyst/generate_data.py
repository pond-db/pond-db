#!/usr/bin/env python3
"""Generate sample sales data with realistic patterns and anomalies.

Creates sample_data/sales.csv with 10,000 rows spanning 2024-2025.
Includes a revenue spike in West region during March 2025 (3x normal)
for the analyst agent to discover.

Run once:  python generate_data.py
"""

from __future__ import annotations

import csv
import os
import random
from datetime import date, timedelta

SEED = 42
ROWS = 10_000
OUTPUT = os.path.join(os.path.dirname(__file__), "sample_data", "sales.csv")

REGIONS = ["West", "East", "Central", "South", "International"]
PRODUCTS = ["Pro", "Starter", "Enterprise", "Student", "Free Trial"]

# Revenue ranges by product (min, max)
REVENUE_RANGES: dict[str, tuple[int, int]] = {
    "Enterprise": (500, 5000),
    "Pro": (100, 2000),
    "Starter": (50, 500),
    "Student": (10, 100),
    "Free Trial": (0, 50),
}

# Anomaly config: West region in March 2025 gets 3x revenue
ANOMALY_REGION = "West"
ANOMALY_YEAR = 2025
ANOMALY_MONTH = 3
ANOMALY_MULTIPLIER = 3


def main() -> None:
    random.seed(SEED)

    start_date = date(2024, 1, 1)
    end_date = date(2025, 12, 31)
    date_range = (end_date - start_date).days

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "region", "product", "revenue", "quantity"])

        for _ in range(ROWS):
            d = start_date + timedelta(days=random.randint(0, date_range))
            region = random.choice(REGIONS)
            product = random.choice(PRODUCTS)
            rev_min, rev_max = REVENUE_RANGES[product]
            revenue = round(random.uniform(rev_min, rev_max), 2)
            quantity = random.randint(1, 100)

            # Apply anomaly: 3x revenue for West in March 2025
            if (
                region == ANOMALY_REGION
                and d.year == ANOMALY_YEAR
                and d.month == ANOMALY_MONTH
            ):
                revenue = round(revenue * ANOMALY_MULTIPLIER, 2)

            writer.writerow([d.isoformat(), region, product, revenue, quantity])

    print(f"Generated {ROWS} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
