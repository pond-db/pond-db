"""FastAPI application — PondDB server entry point."""

from fastapi import FastAPI

from ponddb import __version__

app = FastAPI(
    title="PondDB",
    version=__version__,
    description="Lightweight self-hosted DuckDB compute platform",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "sessions": 0}
