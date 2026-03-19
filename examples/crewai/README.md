# CrewAI + PondDB Memory Demo

Three AI agents (Researcher, Writer, Editor) collaborate on a blog post by sharing
findings through PondDB's memory API.  No data passes through LLM prompts — every
fact is written to and read from PondDB.

## Why PondDB for CrewAI?

CrewAI agents normally pass context through task outputs embedded in prompts.
This approach breaks down when:

- Findings are too long for a prompt window
- You want an audit trail of what each agent knew and when
- Multiple downstream agents need to query the same structured facts

PondDB gives every agent a shared memory store with typed entries, importance
scores, and SQL-queryable history.

```
Researcher                  Writer                   Editor
    |                          |                        |
    |-- remember(semantic) --> PondDB                   |
    |                          |                        |
    |                          |-- recall(semantic) --> PondDB
    |                          |-- remember(episodic) -> PondDB
    |                          |                        |
    |                          |                        |-- recall(episodic) --> PondDB
    |                          |                        |-- recall(semantic) --> PondDB
    |                          |                        |-- remember(procedural) -> PondDB
    |                          |                        |
    +------------- run_analytics() via /pondapi/execute ----------+
```

## Memory types used

| Agent      | memory_type | access_scope | Purpose                        |
|------------|-------------|--------------|--------------------------------|
| Researcher | semantic    | workgroup    | Reusable facts about the topic |
| Writer     | episodic    | workgroup    | Draft paragraph                |
| Editor     | procedural  | namespace    | Polished reusable template     |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start PondDB

```bash
# from the db-engine repo root
docker compose up -d
```

### 3. Set environment variables

```bash
export PONDDB_URL=http://localhost:8432
export PONDDB_API_KEY=your-ponddb-api-key

# LLM key — CrewAI defaults to OpenAI; swap for any supported provider
export OPENAI_API_KEY=your-openai-key
```

### 4. Run the demo

```bash
python demo.py
```

## Code example

```python
import os
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

PONDDB_URL = os.getenv("PONDDB_URL", "http://localhost:8432")
PONDDB_API_KEY = os.getenv("PONDDB_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"X-API-Key": PONDDB_API_KEY, "Content-Type": "application/json"}


class RememberInput(BaseModel):
    agent_id: str = Field(..., description="Unique agent identifier.")
    memory_type: str = Field(..., description="working|episodic|semantic|procedural|shared")
    content: dict = Field(..., description="JSONB payload to store.")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    access_scope: str = Field(default="workgroup", description="private|workgroup|namespace")


class RememberTool(BaseTool):
    name: str = "remember"
    description: str = "Persist a finding to PondDB memory."
    args_schema = RememberInput

    def _run(self, agent_id, memory_type, content, importance=0.5, access_scope="workgroup"):
        resp = httpx.post(
            f"{PONDDB_URL}/memories",
            json={"agent_id": agent_id, "memory_type": memory_type,
                  "content": content, "importance": importance,
                  "access_scope": access_scope},
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return f"Memory stored (id={resp.json()['id']})."
        return f"Failed ({resp.status_code}): {resp.text}"


class RecallInput(BaseModel):
    content_contains: str | None = Field(default=None)
    memory_type: str | None = Field(default=None)
    min_importance: float = Field(default=0.0)
    limit: int = Field(default=10)


class RecallTool(BaseTool):
    name: str = "recall"
    description: str = "Search PondDB memory for stored findings."
    args_schema = RecallInput

    def _run(self, content_contains=None, memory_type=None, min_importance=0.0, limit=10):
        params = {"min_importance": min_importance, "limit": limit}
        if content_contains:
            params["content_contains"] = content_contains
        if memory_type:
            params["memory_type"] = memory_type

        resp = httpx.get(f"{PONDDB_URL}/memories/search", params=params, headers=_headers())
        memories = resp.json()
        if not memories:
            return "No relevant memories found."
        return "\n".join(
            f"[{m['agent_id']}/{m['memory_type']}] {m['content']}" for m in memories
        )
```

## Running tests

The integration tests use mocked HTTP calls — no PondDB instance required:

```bash
cd /path/to/db-engine
.venv/bin/pytest examples/crewai/test_integration.py -v
```

## PondDB Memory API reference

All endpoints require the `X-API-Key` header.

| Method | Path | Body / Params |
|--------|------|---------------|
| POST | `/memories` | `{agent_id, memory_type, content, importance, access_scope}` |
| GET | `/memories/search` | `?content_contains=&memory_type=&min_importance=&limit=` |
| GET | `/memories/{id}` | — |
| POST | `/memories/{id}/feedback` | `{reward: float}` (-1 to 1) |
| POST | `/pondapi/execute` | `{session_id, sql}` |

Valid `memory_type` values: `working`, `episodic`, `semantic`, `procedural`, `shared`

Valid `access_scope` values: `private`, `workgroup`, `namespace`
