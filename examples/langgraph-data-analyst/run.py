#!/usr/bin/env python3
"""LangGraph + PondDB Demo: Multi-Agent Sales Analysis.

Three AI agents collaborate through PondDB to analyze sales data.
No data passes through the LLM context — all data flows through SQL.

Prerequisites:
  1. PondDB running: docker compose up -d (in pond-db repo root)
  2. pip install -r requirements.txt
  3. cp .env.example .env  (fill in your keys)

Usage:
  python run.py
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

load_dotenv()

from graph import graph  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402


def main() -> None:
    print()
    print("  =========================================")
    print("  LangGraph + PondDB Multi-Agent Analysis")
    print("  =========================================")
    print()
    print("  Three agents collaborating through PondDB:")
    print("    1. Data Engineer  — uploads CSV, verifies schema")
    print("    2. Analyst        — queries data, saves insights")
    print("    3. Report Writer  — reads queries, writes summary")
    print()
    print("  All data flows through PondDB SQL — not LLM context.")
    print()

    start = time.time()

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Analyze the sales data in sample_data/sales.csv. "
                        "Upload it to PondDB, run analysis queries, and "
                        "produce an executive summary with top regions, "
                        "monthly trends, and any anomalies. "
                        "Save all important queries to PondDB."
                    )
                )
            ]
        }
    )

    elapsed = time.time() - start

    # Print the final report (last message from report writer)
    print()
    print("  =========================================")
    print("  EXECUTIVE REPORT")
    print("  =========================================")
    print()
    print(result["messages"][-1].content)
    print()
    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Total messages exchanged: {len(result['messages'])}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
