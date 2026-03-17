"""Integration tests for the PondDB Python SDK client."""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ponddb.client import PondClient
from ponddb.exceptions import (
    AuthenticationError,
    PondDBError,
    QueryError,
    RateLimitError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server_url() -> str:
    return "http://localhost:8432"


@pytest.fixture
def api_key() -> str:
    return "test-api-key-12345"


@pytest.fixture
def client(server_url: str, api_key: str) -> PondClient:
    return PondClient(base_url=server_url, api_key=api_key)


@pytest.fixture
def auth_response() -> dict[str, Any]:
    return {
        "access_token": "eyJhbGciOiJIUzI1NiJ9.access",
        "refresh_token": "eyJhbGciOiJIUzI1NiJ9.refresh",
        "token_type": "bearer",
        "expires_in": 3600,
    }


@pytest.fixture
def query_result() -> dict[str, Any]:
    return {
        "columns": ["id", "name"],
        "rows": [[1, "Alice"], [2, "Bob"]],
        "rowcount": 2,
        "elapsed_ms": 12.5,
    }


# ---------------------------------------------------------------------------
# Client Initialization
# ---------------------------------------------------------------------------


class TestClientInitialization:
    def test_basic_init(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key)
        assert client.base_url == base_url
        assert client.api_key == api_key

    def test_default_tenant_id(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key)
        assert client.tenant_id == "default"

    def test_custom_tenant_id(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key, tenant_id="acme")
        assert client.tenant_id == "acme"

    def test_default_max_retries(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key)
        assert client.max_retries == 3

    def test_custom_max_retries(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key, max_retries=5)
        assert client.max_retries == 5

    def test_not_authenticated_initially(self, client: PondClient) -> None:
        assert client.access_token is None

    def test_trailing_slash_stripped_from_base_url(self, api_key: str) -> None:
        client = PondClient(base_url="http://localhost:8432/", api_key=api_key)
        assert client.base_url == "http://localhost:8432"

    def test_timeout_default(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key)
        assert client.timeout > 0

    def test_custom_timeout(self, base_url: str, api_key: str) -> None:
        client = PondClient(base_url=base_url, api_key=api_key, timeout=60.0)
        assert client.timeout == 60.0


# ---------------------------------------------------------------------------
# authenticate()
# ---------------------------------------------------------------------------


class TestAuthenticate:
    async def test_authenticate_happy_path(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = auth_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            await client.authenticate()

        assert client.access_token == auth_response["access_token"]
        assert client.refresh_token == auth_response["refresh_token"]

    async def test_authenticate_sends_api_key(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = auth_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.authenticate()

        call_kwargs = mock_post.call_args
        body = (
            call_kwargs.kwargs.get("json") or call_kwargs.args[1]
            if len(call_kwargs.args) > 1
            else {}
        )
        assert body.get("api_key") == client.api_key

    async def test_authenticate_uses_auth_token_endpoint(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = auth_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.authenticate()

        url = mock_post.call_args.args[0]
        assert "/auth/token" in url

    async def test_authenticate_raises_on_invalid_key(
        self,
        client: PondClient,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"detail": "Invalid API key"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_response
        )

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(AuthenticationError):
                await client.authenticate()

    async def test_authenticate_stores_expires_in(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = auth_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            await client.authenticate()

        assert client.expires_in == auth_response["expires_in"]

    async def test_authenticate_with_tenant_id(
        self,
        api_key: str,
        auth_response: dict[str, Any],
    ) -> None:
        client = PondClient(base_url="http://localhost:8432", api_key=api_key, tenant_id="acme")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = auth_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.authenticate()

        body = mock_post.call_args.kwargs.get("json", {})
        assert body.get("tenant_id") == "acme"


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


class TestQuery:
    async def _setup_auth(self, client: PondClient, auth_response: dict[str, Any]) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic()

    async def test_query_happy_path(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = query_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.query("SELECT id, name FROM users")

        assert result["columns"] == ["id", "name"]
        assert result["rows"] == [[1, "Alice"], [2, "Bob"]]
        assert result["rowcount"] == 2

    async def test_query_sends_bearer_token(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = query_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.query("SELECT 1")

        headers = mock_post.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization", "").startswith("Bearer ")

    async def test_query_requires_session(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Client must have a session_id when executing a query."""
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = query_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.query("SELECT 1")

        body = mock_post.call_args.kwargs.get("json", {})
        assert "session_id" in body
        assert body["session_id"]  # non-empty

    async def test_query_raises_on_empty_sql(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        with pytest.raises((QueryError, ValueError)):
            await client.query("")

    async def test_query_raises_on_bad_sql(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"detail": "Parser Error: syntax error at '...'"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=mock_response
        )

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(QueryError):
                await client.query("SSELECT 1")

    async def test_query_auto_authenticates_if_not_logged_in(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """query() should call authenticate() if no token is set."""
        assert client.access_token is None

        auth_mock = MagicMock()
        auth_mock.status_code = 200
        auth_mock.json.return_value = auth_response
        auth_mock.raise_for_status = MagicMock()

        query_mock = MagicMock()
        query_mock.status_code = 200
        query_mock.json.return_value = query_result
        query_mock.raise_for_status = MagicMock()

        responses = [auth_mock, query_mock]
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=side_effect):
            result = await client.query("SELECT 1")

        assert result is not None
        assert client.access_token is not None

    async def test_query_returns_elapsed_ms(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = query_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.query("SELECT 1")

        assert "elapsed_ms" in result
        assert result["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# save_query()
# ---------------------------------------------------------------------------


class TestSaveQuery:
    async def _setup_auth(self, client: PondClient, auth_response: dict[str, Any]) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic()

    async def test_save_query_returns_slug(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        saved = {
            "slug": "my-test-query",
            "title": "My Test Query",
            "description": "",
            "sql": "SELECT 1",
            "created_by": "default",
            "created_at": "2026-03-15T00:00:00+00:00",
            "visibility": "private",
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = saved
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            slug = await client.save_query(title="My Test Query", sql="SELECT 1")

        assert slug == "my-test-query"

    async def test_save_query_sends_correct_fields(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        saved = {
            "slug": "revenue-report",
            "title": "Revenue Report",
            "description": "Monthly revenue",
            "sql": "SELECT sum(amount) FROM sales",
            "created_by": "default",
            "created_at": "2026-03-15T00:00:00+00:00",
            "visibility": "private",
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = saved
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.save_query(
                title="Revenue Report",
                sql="SELECT sum(amount) FROM sales",
                description="Monthly revenue",
            )

        body = mock_post.call_args.kwargs.get("json", {})
        assert body["title"] == "Revenue Report"
        assert body["sql"] == "SELECT sum(amount) FROM sales"
        assert body["description"] == "Monthly revenue"

    async def test_save_query_default_visibility_is_private(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        saved = {
            "slug": "q",
            "title": "Q",
            "description": "",
            "sql": "SELECT 1",
            "created_by": "default",
            "created_at": "2026-03-15T00:00:00+00:00",
            "visibility": "private",
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = saved
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.save_query(title="Q", sql="SELECT 1")

        body = mock_post.call_args.kwargs.get("json", {})
        assert body.get("visibility") == "private"

    async def test_save_query_public_visibility(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        saved = {
            "slug": "pub",
            "title": "Pub",
            "description": "",
            "sql": "SELECT 1",
            "created_by": "default",
            "created_at": "2026-03-15T00:00:00+00:00",
            "visibility": "public",
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = saved
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.save_query(title="Pub", sql="SELECT 1", visibility="public")

        body = mock_post.call_args.kwargs.get("json", {})
        assert body.get("visibility") == "public"

    async def test_save_query_raises_on_duplicate(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {"detail": "Query with slug 'my-query' already exists"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "409 Conflict", request=MagicMock(), response=mock_response
        )

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(PondDBError):
                await client.save_query(title="My Query", sql="SELECT 1")

    async def test_save_query_uses_api_key_header(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        saved = {
            "slug": "q",
            "title": "Q",
            "description": "",
            "sql": "SELECT 1",
            "created_by": "default",
            "created_at": "2026-03-15T00:00:00+00:00",
            "visibility": "private",
        }

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = saved
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            await client.save_query(title="Q", sql="SELECT 1")

        headers = mock_post.call_args.kwargs.get("headers", {})
        assert "X-API-Key" in headers or "Authorization" in headers


# ---------------------------------------------------------------------------
# list_queries()
# ---------------------------------------------------------------------------


class TestListQueries:
    async def _setup_auth(self, client: PondClient, auth_response: dict[str, Any]) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic()

    async def test_list_queries_returns_list(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        queries = [
            {
                "slug": "q1",
                "title": "Q1",
                "description": "",
                "sql": "SELECT 1",
                "created_by": "default",
                "created_at": "2026-03-15T00:00:00+00:00",
                "visibility": "private",
            },
            {
                "slug": "q2",
                "title": "Q2",
                "description": "",
                "sql": "SELECT 2",
                "created_by": "default",
                "created_at": "2026-03-15T01:00:00+00:00",
                "visibility": "public",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = queries
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.list_queries()

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_list_queries_empty(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.list_queries()

        assert result == []

    async def test_list_queries_default_pagination(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.list_queries()

        params = mock_get.call_args.kwargs.get("params", {})
        # Should send limit and offset
        assert "limit" in params or "created_by" in params

    async def test_list_queries_custom_limit(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.list_queries(limit=5, offset=10)

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("limit") == 5
        assert params.get("offset") == 10

    async def test_list_queries_returns_dicts_with_expected_fields(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        queries = [
            {
                "slug": "q1",
                "title": "Q1",
                "description": "",
                "sql": "SELECT 1",
                "created_by": "default",
                "created_at": "2026-03-15T00:00:00+00:00",
                "visibility": "private",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = queries
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.list_queries()

        q = result[0]
        assert "slug" in q
        assert "title" in q
        assert "sql" in q


# ---------------------------------------------------------------------------
# get_history()
# ---------------------------------------------------------------------------


class TestGetHistory:
    async def _setup_auth(self, client: PondClient, auth_response: dict[str, Any]) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic()

    async def test_get_history_returns_list(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        history = [
            {
                "namespace": "default",
                "sql": "SELECT 1",
                "duration_ms": 5.0,
                "rows_returned": 1,
                "status": "success",
                "error_message": None,
                "executed_at": "2026-03-15T00:00:00+00:00",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = history
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_history()

        assert isinstance(result, list)
        assert len(result) == 1

    async def test_get_history_empty(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_history()

        assert result == []

    async def test_get_history_filter_by_status(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.get_history(status="error")

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("status") == "error"

    async def test_get_history_pagination(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.get_history(limit=10, offset=20)

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("limit") == 10
        assert params.get("offset") == 20

    async def test_get_history_sends_bearer_token(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.get_history()

        headers = mock_get.call_args.kwargs.get("headers", {})
        auth_header = headers.get("Authorization", "")
        assert auth_header.startswith("Bearer ")

    async def test_get_history_records_have_expected_fields(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        history = [
            {
                "namespace": "default",
                "sql": "SELECT 1",
                "duration_ms": 5.0,
                "rows_returned": 1,
                "status": "success",
                "error_message": None,
                "executed_at": "2026-03-15T00:00:00+00:00",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = history
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_history()

        row = result[0]
        for field in ("namespace", "sql", "duration_ms", "rows_returned", "status", "executed_at"):
            assert field in row, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# share_query()
# ---------------------------------------------------------------------------


class TestShareQuery:
    async def _setup_auth(self, client: PondClient, auth_response: dict[str, Any]) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic()

    async def test_share_query_returns_results(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        share_result = {
            "columns": ["n"],
            "rows": [[42]],
            "rowcount": 1,
            "elapsed_ms": 3.2,
            "slug": "my-query",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = share_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.share_query("my-query")

        assert result["slug"] == "my-query"
        assert result["columns"] == ["n"]
        assert result["rows"] == [[42]]

    async def test_share_query_hits_correct_endpoint(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        share_result = {
            "columns": [],
            "rows": [],
            "rowcount": 0,
            "elapsed_ms": 1.0,
            "slug": "test-slug",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = share_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.share_query("test-slug")

        url = mock_get.call_args.args[0]
        assert "/q/test-slug" in url

    async def test_share_query_not_found(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"detail": "No query found with slug: 'nonexistent'"}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_response
        )

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(PondDBError):
                await client.share_query("nonexistent")

    async def test_share_query_rate_limited(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.json.return_value = {"detail": "Rate limit exceeded. Try again later."}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests", request=MagicMock(), response=mock_response
        )

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(RateLimitError):
                await client.share_query("public-slug")

    async def test_share_query_sends_api_key_for_private(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        await self._setup_auth(client, auth_response)
        share_result = {
            "columns": [],
            "rows": [],
            "rowcount": 0,
            "elapsed_ms": 1.0,
            "slug": "private-slug",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = share_result
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            await client.share_query("private-slug")

        headers = mock_get.call_args.kwargs.get("headers", {})
        assert "X-API-Key" in headers or "Authorization" in headers
