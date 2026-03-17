# LangGraph + PondDB: Multi-Agent Data Analysis

Three AI agents collaborate through PondDB to analyze sales data — without passing data through the LLM context window.

## Why PondDB for agents?

Multi-agent systems need shared state. Most frameworks stuff data into prompts or pass it through function returns. PondDB gives agent teams a shared SQL database where:

- Agents **write structured results** (not crammed into prompts)
- Other agents **query those results** (not re-processing from scratch)
- Every number comes from a **real query** (not hallucinated)

## The Agents

| Agent | Role | PondDB Tools Used |
|-------|------|-------------------|
| Data Engineer | Uploads CSV, verifies schema | `upload_csv`, `list_tables`, `query` |
| Analyst | Runs analysis queries, saves them | `query`, `save_query` |
| Report Writer | Reads saved queries, writes report | `get_saved_queries`, `query` |

## How agents share state through PondDB

```
Data Engineer          Analyst              Report Writer
     │                    │                      │
     ├─ upload_csv ───▶ PondDB                   │
     ├─ query(COUNT) ──▶ PondDB                  │
     │                    │                      │
     │                    ├─ query(top regions) ──▶ PondDB
     │                    ├─ save_query("top_regions")
     │                    ├─ query(monthly trends)▶ PondDB
     │                    ├─ save_query("monthly_trends")
     │                    │                      │
     │                    │                      ├─ get_saved_queries()
     │                    │                      ├─ query(rerun each)
     │                    │                      ├─ writes executive summary
     │                    │                      │
     └────────────────────┴──────────────────────┘
                    PondDB Workgroup
                   (shared SQL state)
```

**No data passes through LLM context.** All data flows through PondDB SQL queries.

## Run it

```bash
# 1. Start PondDB (from pond-db repo root)
cd pond-db && docker compose up -d

# 2. Install deps
cd examples/langgraph-data-analyst
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env: set PONDDB_URL, PONDDB_API_KEY, ANTHROPIC_API_KEY

# 4. Generate sample data (10,000 rows)
python generate_data.py

# 5. Run the multi-agent pipeline
python run.py
```

## What happens

1. **Data Engineer** uploads `sample_data/sales.csv` to PondDB, verifies schema and row count
2. **Analyst** discovers the tables, runs queries for top regions, monthly trends, YoY growth, and anomalies. Saves each query to PondDB.
3. **Report Writer** reads saved queries from PondDB, re-runs them for fresh numbers, and writes an executive summary where every number is backed by a SQL result.

The key insight: the Analyst's saved queries become the Report Writer's input — through PondDB, not through the LLM context window.

## Sample data

`sales.csv` contains 10,000 rows spanning 2024-2025:

| Column | Type | Example |
|--------|------|---------|
| date | DATE | 2024-06-15 |
| region | VARCHAR | West, East, Central, South, International |
| product | VARCHAR | Pro, Starter, Enterprise, Student, Free Trial |
| revenue | DECIMAL | 10.00 - 5,000.00 |
| quantity | INTEGER | 1 - 100 |

There's a hidden anomaly: the **West region in March 2025** has 3x normal revenue. The analyst agent should find it.

## Requirements

- PondDB running (Docker or local)
- Python 3.10+
- Anthropic API key (for Claude)
- PondDB API key
