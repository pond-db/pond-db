"""PondDB Python client — embeddable library API."""

from __future__ import annotations

from typing import Any


class PondDB:
    """Lightweight DuckDB compute client.

    Can be used as a library (no server required) or as a client
    pointing at a running PondDB server.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8432",
        api_key: str | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.token = token
        self._session_id: str | None = None

    def query(self, sql: str, format: str = "json") -> Any:
        """Execute a SQL query and return results."""
        import httpx

        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key

        resp = httpx.post(
            f"{self.base_url}/query",
            json={"session_id": self._session_id, "sql": sql, "format": format},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    def connect(self) -> "PondDB":
        """Create a new session and return self for chaining."""
        import httpx

        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key

        resp = httpx.post(f"{self.base_url}/session", headers=headers)
        resp.raise_for_status()
        self._session_id = resp.json().get("session_id")
        return self

    def close(self) -> None:
        """Destroy the current session."""
        if not self._session_id:
            return
        import httpx

        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.api_key:
            headers["X-API-Key"] = self.api_key

        httpx.delete(f"{self.base_url}/session/{self._session_id}", headers=headers)
        self._session_id = None

    def __enter__(self) -> "PondDB":
        return self.connect()

    def __exit__(self, *_: Any) -> None:
        self.close()
