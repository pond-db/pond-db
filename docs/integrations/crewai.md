# PondDB + CrewAI

CrewAI (44K+ GitHub stars) is role-based multi-agent orchestration. PondDB workgroups map naturally to crew roles — each agent stores memories that other agents can search.

## Setup

```bash
pip install crewai httpx
# PondDB running at localhost:8432
export OPENAI_API_KEY=your-key
```

## Define Tools

```python
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type
import httpx

PONDDB = "http://localhost:8432"
headers = {"X-API-Key": "your-ponddb-api-key", "Content-Type": "application/json"}

class RememberInput(BaseModel):
    content: str = Field(description="What to remember")
    importance: float = Field(default=0.7, description="0.0 to 1.0")

class RememberTool(BaseTool):
    name: str = "Store Memory"
    description: str = "Store a finding in the team's shared memory database."
    args_schema: Type[BaseModel] = RememberInput

    def _run(self, content: str, importance: float = 0.7) -> str:
        resp = httpx.post(f"{PONDDB}/memories", json={
            "agent_id": "crewai-researcher",
            "memory_type": "shared",
            "content": {"text": content},
            "importance": importance,
            "access_scope": "workgroup"
        }, headers=headers)
        return f"Stored in team memory (id: {resp.json()['id'][:8]}...)"

class RecallInput(BaseModel):
    query: str = Field(description="What to search for")

class RecallTool(BaseTool):
    name: str = "Search Memory"
    description: str = "Search the team's shared memory for past findings."
    args_schema: Type[BaseModel] = RecallInput

    def _run(self, query: str) -> str:
        resp = httpx.get(f"{PONDDB}/memories/search", params={
            "content_contains": query, "limit": 10
        }, headers=headers)
        memories = resp.json()
        if not memories:
            return "No relevant memories found in team database."
        return "\n".join(
            f"[importance: {m['importance']}] {m['content']['text']}"
            for m in memories
        )
```

## Create Agents and Tasks

```python
from crewai import Agent, Task, Crew, Process

remember_tool = RememberTool()
recall_tool = RecallTool()

researcher = Agent(
    role="Senior Research Analyst",
    goal="Find key facts and store them in team memory",
    backstory="Expert researcher who stores findings in shared memory.",
    tools=[remember_tool, recall_tool],
    verbose=True
)

writer = Agent(
    role="Content Writer",
    goal="Write content based on research from team memory",
    backstory="Writer who always checks team memory before writing.",
    tools=[recall_tool],
    verbose=True
)

research_task = Task(
    description="Research 'self-hosted databases'. Store each finding in team memory.",
    expected_output="Summary with confirmation each fact was stored.",
    agent=researcher
)

writing_task = Task(
    description="Search team memory, then write a 300-word article from the findings.",
    expected_output="A polished article sourced from team memory.",
    agent=writer
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    process=Process.sequential,
    verbose=True
)

result = crew.kickoff()
```

## Key Features

- **Role-based sharing**: Each agent stores memories that the next agent reads
- **Persistent across crews**: Run the crew again — past research is still there
- **SQL analytics**: See which agent stored the most useful memories

## Post-Crew Analytics

```python
analytics = httpx.post(f"{PONDDB}/pondapi/execute", json={
    "sql": """SELECT agent_id, COUNT(*) as memories_stored,
              ROUND(AVG(importance), 2) as avg_importance
              FROM agent_memories WHERE deleted_at IS NULL
              GROUP BY agent_id ORDER BY memories_stored DESC"""
}, headers=headers)
```

## Full Demo

See [`examples/crewai/demo.py`](../../examples/crewai/demo.py)
