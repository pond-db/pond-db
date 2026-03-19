# Google ADK + PondDB: Persistent Memory Agent

An agent built with Google's Agent Development Kit (ADK) that uses PondDB as its
persistent memory store. Memories survive across sessions, can be searched by
content, and are rated by usefulness using reinforcement signals.

## Why PondDB for ADK agents?

Google ADK agents are stateless by default — every session starts fresh.
PondDB gives them durable, queryable memory:

| Need | PondDB solution |
|------|-----------------|
| Store a decision | `POST /memories` with `memory_type=episodic` |
| Find relevant context | `GET /memories/search?content_contains=...` |
| Reinforce good recall | `POST /memories/{id}/feedback` with reward |
| Audit memory usage | `POST /pondapi/execute` with SQL |

## Memory types

| Type | Use when |
|------|----------|
| `working` | Temporary scratchpad for the current task |
| `episodic` | Past events and interactions |
| `semantic` | Facts, definitions, reference knowledge |
| `procedural` | How-to steps and learned workflows |
| `shared` | Information meant for other agents in the same workgroup |

## Setup

### 1. Start PondDB

```bash
# From the db-engine repo root
docker compose up -d
```

PondDB starts on `http://localhost:8432` by default.

### 2. Install dependencies

```bash
cd examples/google-adk
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
export PONDDB_URL="http://localhost:8432"
export PONDDB_API_KEY="pk_your_api_key_here"
export GOOGLE_API_KEY="your_google_ai_key"   # needed by google-adk
```

Or create a `.env` file (never commit it):

```
PONDDB_URL=http://localhost:8432
PONDDB_API_KEY=pk_your_api_key_here
GOOGLE_API_KEY=your_google_ai_key
```

### 4. Run the agent

```bash
adk run demo.py
```

Or interact with the agent programmatically:

```python
from demo import memory_agent

# The agent uses the four PondDB tools automatically when prompted.
# Example via adk runner:
#   adk run demo.py
```

## The four tools

### `remember` — store a memory

```python
from demo import remember

result = remember(
    agent_id="my-agent",
    memory_type="semantic",
    content={"fact": "PondDB uses DuckDB under the hood"},
    importance=0.9,
    access_scope="workgroup",
)
# "Memory stored successfully. id=mem-abc-123 type=semantic"
```

### `recall` — search memories

```python
from demo import recall

result = recall(
    content_contains="DuckDB",
    memory_type="semantic",
    min_importance=0.7,
    limit=5,
)
# "Found 1 memory/memories:
#   [mem-abc-123] (semantic, importance=0.9) {"fact": "PondDB uses DuckDB..."}"
```

### `rate_memory` — give feedback

```python
from demo import rate_memory

result = rate_memory(memory_id="mem-abc-123", reward=0.8)
# "Feedback recorded for memory mem-abc-123 (reward=0.8)"
```

Rewards range from `-1.0` (harmful recall) to `1.0` (perfect recall).
PondDB uses these signals to decay low-utility memories over time.

### `memory_analytics` — SQL analytics

```python
from demo import memory_analytics

result = memory_analytics(session_id="sess-demo-1")
# "Memory analytics (2 rows):
#   {'memory_type': 'semantic', 'count': 12, 'avg_importance': 0.83}
#   {'memory_type': 'episodic', 'count': 4, 'avg_importance': 0.71}"
```

## Running the tests

The integration tests mock all httpx calls — no running PondDB required:

```bash
cd /path/to/db-engine
.venv/bin/python -m pytest examples/google-adk/test_integration.py -v
```

## Full example: agent remembering a decision

```python
import os
from demo import remember, recall, rate_memory

os.environ["PONDDB_URL"] = "http://localhost:8432"
os.environ["PONDDB_API_KEY"] = "pk_..."

# Store a decision from today's planning session
result = remember(
    agent_id="planning-agent",
    memory_type="episodic",
    content={
        "decision": "Use PondDB for all agent state",
        "rationale": "Avoids context-window bloat, enables cross-agent sharing",
        "date": "2026-03-19",
    },
    importance=0.95,
    access_scope="workgroup",
)
print(result)
# Memory stored successfully. id=mem-001 type=episodic

# Later: search for it
result = recall(content_contains="PondDB", memory_type="episodic", min_importance=0.8, limit=3)
print(result)
# Found 1 memory/memories:
#   [mem-001] (episodic, importance=0.95) {"decision": "Use PondDB..."}

# Rate it as useful
rate_memory(memory_id="mem-001", reward=1.0)
```

## Access scopes

| Scope | Who can see it |
|-------|---------------|
| `private` | Only the agent that created it |
| `workgroup` | All agents in the same workgroup |
| `namespace` | All agents in the namespace (across workgroups, with grants) |

## Requirements

- PondDB running (Docker or local binary)
- Python 3.10+
- Google ADK (`pip install google-adk`)
- PondDB API key (create one in the PondDB dashboard)
