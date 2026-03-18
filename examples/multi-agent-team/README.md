# Multi-Agent Team Demo

A 3-agent team demonstrating PondDB's memory sharing and causal chains.

## Agents

| Agent | Workgroup | Role |
|-------|-----------|------|
| researcher | research-team | Finds customer data, writes shared findings |
| analyst | research-team | Reads findings, writes analysis with causal link |
| writer | writing-team | Reads analysis via cross-workgroup grant, drafts emails |

## Run

```bash
# Start PondDB
docker compose up -d

# Run the demo
python multi_agent_demo.py [your-api-key]
```

## What it demonstrates

1. **Memory types**: `shared` for team knowledge, `episodic` for drafts
2. **Causal chains**: research → analysis → writing linked via `causal_parent_id`
3. **Utility feedback**: user rates memories, boosting high-value findings
4. **Access logging**: every operation tracked in `memory_access_log`

## The analytics queries you can run after

```sql
-- Which agent created most memories?
SELECT agent_id, COUNT(*) as total
FROM agent_memories WHERE deleted_at IS NULL
GROUP BY agent_id ORDER BY total DESC;

-- Show the causal chain
WITH RECURSIVE chain AS (
  SELECT id, agent_id, content, causal_parent_id, 0 as depth
  FROM agent_memories WHERE causal_parent_id IS NULL AND deleted_at IS NULL
  UNION ALL
  SELECT m.id, m.agent_id, m.content, m.causal_parent_id, c.depth + 1
  FROM agent_memories m JOIN chain c ON m.causal_parent_id = c.id
  WHERE m.deleted_at IS NULL AND c.depth < 10
)
SELECT depth, agent_id, content FROM chain ORDER BY depth;

-- Cross-workgroup access audit
SELECT agent_id, action, grant_id, source_workgroup_id, COUNT(*)
FROM memory_access_log
WHERE grant_id IS NOT NULL
GROUP BY 1, 2, 3, 4;
```
