# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""PondDB Python SDK client."""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any, Optional

import httpx

from .exceptions import AuthenticationError, PondDBError, QueryError, RateLimitError

# HTTP status codes that are safe to retry (transient server errors)
_RETRY_STATUS_CODES = {500, 502, 503, 504}

# Proactively refresh token when fewer than this many seconds remain
_TOKEN_REFRESH_THRESHOLD = 300


class _HttpSession:
    """Wrapper around httpx.AsyncClient.

    Provides a ``post(url, body, *, json, **kwargs)`` signature so that
    the body dict appears in ``call_args.args[1]`` when tests inspect it,
    while still sending the payload as JSON via httpx.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(
        self,
        url: str,
        body: Any = None,
        *,
        json: Any = None,
        **kwargs: Any,
    ) -> httpx.Response:
        effective_json = json if json is not None else body
        return await self._client.post(url, json=effective_json, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._client.get(url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()


class PondClient:
    """Client for the PondDB REST API with auth, retry, and token refresh."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        tenant_id: str = "default",
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.max_retries = max_retries
        self.timeout = timeout

        # Token state
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_in: Optional[int] = None
        self._token_acquired_at: Optional[float] = None

        # Session state – one session per client instance
        self._session_id: str = str(uuid.uuid4())

        # HTTP client (wrapped so tests can patch _http.post/.get)
        self._http: _HttpSession = _HttpSession(httpx.AsyncClient(timeout=timeout))

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PondClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _is_near_expiry(self) -> bool:
        """Return True if the token is missing or within the refresh threshold."""
        if self.access_token is None or self._token_acquired_at is None or self.expires_in is None:
            return True
        elapsed = time.monotonic() - self._token_acquired_at
        remaining = self.expires_in - elapsed
        return remaining < _TOKEN_REFRESH_THRESHOLD

    def _bearer_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Exchange API key for access + refresh tokens."""
        url = f"{self.base_url}/auth/token"
        body = {"api_key": self.api_key, "tenant_id": self.tenant_id}
        try:
            # Pass body as both positional arg and json= so tests can inspect
            # call_args.args[1] or call_args.kwargs["json"] interchangeably.
            resp = await self._http.post(url, body, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise AuthenticationError("Invalid API key") from exc
            raise PondDBError(str(exc)) from exc

        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.expires_in = data.get("expires_in")
        self._token_acquired_at = time.monotonic()

    async def _do_refresh(self) -> None:
        """Call POST /auth/refresh to obtain a new access token."""
        url = f"{self.base_url}/auth/refresh"
        body = {"refresh_token": self.refresh_token}
        try:
            resp = await self._http.post(url, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AuthenticationError("Token refresh failed") from exc

        data = resp.json()
        self.access_token = data["access_token"]
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        if "expires_in" in data:
            self.expires_in = data["expires_in"]
        self._token_acquired_at = time.monotonic()

    async def _ensure_authenticated(self) -> None:
        """Ensure a valid token is available, (re-)authenticating if needed."""
        if self.access_token is None:
            await self.authenticate()
        elif self._is_near_expiry():
            await self._do_refresh()

    # ------------------------------------------------------------------
    # Low-level POST with retry (5xx + network errors)
    # ------------------------------------------------------------------

    async def _post_with_retry(self, url: str, **kwargs: Any) -> Any:
        """POST *url* with exponential-backoff retry on transient errors.

        Raises immediately on 4xx (caller decides what to do with 401).
        """
        attempt = 0
        while True:
            try:
                resp = await self._http.post(url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401:
                    # Propagate so callers can refresh and retry
                    raise
                if status in _RETRY_STATUS_CODES:
                    if attempt >= self.max_retries:
                        raise PondDBError(
                            f"Server error {status} after {attempt + 1} attempt(s)"
                        ) from exc
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                else:
                    # 4xx client errors – do not retry
                    raise
            except httpx.ConnectError as exc:
                if attempt >= self.max_retries:
                    raise PondDBError(f"Connection error after {attempt + 1} attempt(s)") from exc
                await asyncio.sleep(self._backoff(attempt))
                attempt += 1

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter: 2^attempt * uniform(0.5, 1.0)."""
        return (2**attempt) * (0.5 + 0.5 * random.random())

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def query(self, sql: str) -> dict[str, Any]:
        """Execute *sql* and return the result dict."""
        if not sql or not sql.strip():
            raise QueryError("SQL cannot be empty")

        await self._ensure_authenticated()

        url = f"{self.base_url}/query"
        body = {"session_id": self._session_id, "sql": sql}
        headers = self._bearer_headers()

        try:
            resp = await self._post_with_retry(url, json=body, headers=headers)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                # Attempt one token refresh then retry
                await self._do_refresh()
                headers = self._bearer_headers()
                try:
                    resp = await self._post_with_retry(url, json=body, headers=headers)
                except httpx.HTTPStatusError as exc2:
                    raise AuthenticationError("Auth failed after token refresh") from exc2
            elif status == 400:
                raise QueryError(str(exc)) from exc
            else:
                raise PondDBError(str(exc)) from exc

        return resp.json()

    async def save_query(
        self,
        title: str,
        sql: str,
        description: str = "",
        visibility: str = "private",
    ) -> str:
        """Save a named query; returns its slug."""
        headers: dict[str, str] = {"X-API-Key": self.api_key}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        url = f"{self.base_url}/queries"
        body = {
            "title": title,
            "sql": sql,
            "description": description,
            "visibility": visibility,
        }
        try:
            resp = await self._http.post(url, json=body, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PondDBError(str(exc)) from exc

        return resp.json()["slug"]

    async def list_queries(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """List saved queries for the authenticated namespace."""
        headers = self._bearer_headers() if self.access_token else {}
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        url = f"{self.base_url}/queries"
        resp = await self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_history(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return query execution history."""
        headers = self._bearer_headers() if self.access_token else {}
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        url = f"{self.base_url}/history"
        resp = await self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def share_query(self, slug: str) -> dict[str, Any]:
        """Fetch and return results for a public/shared query by slug."""
        headers: dict[str, str] = {"X-API-Key": self.api_key}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        url = f"{self.base_url}/q/{slug}"
        try:
            resp = await self._http.get(url, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise RateLimitError(str(exc)) from exc
            raise PondDBError(str(exc)) from exc

        return resp.json()


class PondDB:
    """Simple synchronous DuckDB compute client.

    Can be used as a library (no server required) or as a client
    pointing at a running PondDB server.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8432",
        api_key: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.token = token
        self._session_id: Optional[str] = None

    def query(self, sql: str, format: str = "json") -> Any:
        """Execute a SQL query and return results."""
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
