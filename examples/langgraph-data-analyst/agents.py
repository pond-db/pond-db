"""Agent definitions for the LangGraph + PondDB multi-agent demo.

Three agents collaborate through PondDB as shared state:
  1. Data Engineer — uploads CSV, verifies schema
  2. Analyst — runs analytical queries, saves them
  3. Report Writer — reads saved queries, writes executive summary
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from ponddb_tools import (
    get_saved_queries,
    list_ponddb_tables,
    query_ponddb,
    save_query,
    upload_csv_to_ponddb,
)

llm = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)

# ---------------------------------------------------------------------------
# Agent 1: Data Engineer
# ---------------------------------------------------------------------------

data_engineer = create_react_agent(
    llm,
    tools=[upload_csv_to_ponddb, list_ponddb_tables, query_ponddb],
    state_modifier=(
        "You are a Data Engineer. Your job:\n"
        "1. Upload the CSV dataset to PondDB using upload_csv_to_ponddb\n"
        "2. Verify the schema looks correct using list_ponddb_tables\n"
        "3. Run a quick SELECT COUNT(*) to confirm the row count\n"
        "4. Run SELECT * FROM <table> LIMIT 5 to preview the data\n\n"
        "Report what tables are available, their schemas, and row counts.\n"
        "Do NOT run analysis queries — that's the Analyst's job."
    ),
)

# ---------------------------------------------------------------------------
# Agent 2: Analyst
# ---------------------------------------------------------------------------

analyst = create_react_agent(
    llm,
    tools=[query_ponddb, save_query, list_ponddb_tables],
    state_modifier=(
        "You are a Data Analyst. Your job:\n"
        "1. Check what tables exist in PondDB (don't re-upload anything)\n"
        "2. Run analytical queries to find insights:\n"
        "   - Top 5 regions by total revenue\n"
        "   - Monthly revenue trends (GROUP BY month)\n"
        "   - Top products by revenue\n"
        "   - Year-over-year growth comparison\n"
        "   - Any anomalies (months with unusually high/low revenue)\n"
        "3. Save each important query with a descriptive title\n\n"
        "CRITICAL: Do NOT calculate numbers in your head. ALWAYS use\n"
        "query_ponddb to get real numbers from the database. Every\n"
        "insight must come from an actual SQL query result."
    ),
)

# ---------------------------------------------------------------------------
# Agent 3: Report Writer
# ---------------------------------------------------------------------------

report_writer = create_react_agent(
    llm,
    tools=[get_saved_queries, query_ponddb],
    state_modifier=(
        "You are a Report Writer. Your job:\n"
        "1. Read the saved queries from PondDB using get_saved_queries\n"
        "2. Re-run each saved query to get fresh results\n"
        "3. Write a clear executive summary with:\n"
        "   - Key metrics (total revenue, total orders)\n"
        "   - Top regions ranked by revenue with exact numbers\n"
        "   - Monthly trend description\n"
        "   - Notable anomalies with specific details\n"
        "   - Product performance breakdown\n\n"
        "CRITICAL: Every single number in your report MUST come from\n"
        "a PondDB query result. Do NOT make up or estimate numbers.\n"
        "If you need a number, run a query to get it."
    ),
)
