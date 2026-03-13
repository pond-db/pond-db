# PondDB

Lightweight, self-hosted compute platform wrapping DuckDB with serverless session management.

PondDB gives you MotherDuck-style session lifecycle (auto-suspend, transparent resume, per-session resource limits) on your own hardware — no cloud dependency.

## Quickstart

### Docker (recommended)

```bash
docker compose up
curl http://localhost:8432/health
```

### From source

```bash
pip install -e ".[dev]"
uvicorn ponddb.app:app --host 0.0.0.0 --port 8432
```

### Python library

```python
from ponddb import PondDB

db = PondDB()
result = db.query("SELECT 42 AS answer")
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/session` | POST | Create session |
| `/session/{id}` | DELETE | Destroy session |
| `/query` | POST | Execute SQL |
| `/catalog/mount` | POST | Mount local file |
| `/sessions` | GET | List sessions |
| `/metrics` | GET | Prometheus metrics |

## Configuration

All configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `POND_HOST` | `0.0.0.0` | Server bind host |
| `POND_PORT` | `8432` | Server bind port |
| `POND_JWT_SECRET` | required | JWT signing secret |
| `POND_IDLE_TIMEOUT` | `300` | Session idle timeout (seconds) |
| `POND_DATA_ROOT` | `./data` | Root dir for catalog mounts |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src/ tests/
```

## License

MIT
