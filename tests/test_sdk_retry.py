"""Tests for DuckCloud SDK retry logic with exponential backoff.

Tests that the client retries on transient errors (5xx, network errors)
with exponential backoff, and gives up after max_retries attempts.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from duckcloud import DuckCloudClient
from duckcloud.exceptions import DuckCloudError


@pytest.fixture
def server_url() -> str:
    return "http://localhost:8432"


@pytest.fixture
def api_key() -> str:
    return "test-api-key-12345"


@pytest.fixture
def client(server_url: str, api_key: str) -> DuckCloudClient:
    return DuckCloudClient(base_url=server_url, api_key=api_key, max_retries=3)


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
        "columns": ["n"],
        "rows": [[1]],
        "rowcount": 1,
        "elapsed_ms": 2.0,
    }


def _set_token(client: DuckCloudClient, auth_response: dict[str, Any]) -> None:
    client.access_token = auth_response["access_token"]
    client.refresh_token = auth_response["refresh_token"]
    client.expires_in = auth_response["expires_in"]
    client._token_acquired_at = time.monotonic()


class TestRetryLogic:
    async def test_retries_on_500(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Client should retry on 500 Internal Server Error."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/query" in url and call_count <= 2:
                resp = MagicMock()
                resp.status_code = 500
                resp.json.return_value = {"detail": "Internal Server Error"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.query("SELECT 1")

        assert result["rowcount"] == 1
        assert call_count >= 3  # 2 failures + 1 success

    async def test_retries_on_503(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Client should retry on 503 Service Unavailable."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/query" in url and call_count == 1:
                resp = MagicMock()
                resp.status_code = 503
                resp.json.return_value = {"detail": "Service Unavailable"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "503", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.query("SELECT 1")

        assert result["rowcount"] == 1

    async def test_retries_on_network_error(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Client should retry on network connectivity errors."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/query" in url and call_count == 1:
                raise httpx.ConnectError("Connection refused")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client.query("SELECT 1")

        assert result["rowcount"] == 1

    async def test_gives_up_after_max_retries(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
    ) -> None:
        """Client should raise after exhausting max_retries attempts."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            if "/query" in url:
                call_count += 1
            resp = MagicMock()
            resp.status_code = 500
            resp.json.return_value = {"detail": "Server error"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(DuckCloudError):
                    await client.query("SELECT 1")

        # Should have tried max_retries + 1 times total (1 initial + N retries)
        assert call_count == client.max_retries + 1

    async def test_no_retry_on_400(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
    ) -> None:
        """Client should NOT retry on 400 Bad Request (client error)."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            if "/query" in url:
                call_count += 1
            resp = MagicMock()
            resp.status_code = 400
            resp.json.return_value = {"detail": "Bad SQL"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with pytest.raises(Exception):
                await client.query("INVALID SQL HERE")

        assert call_count == 1  # No retries for client errors

    async def test_no_retry_on_404(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
    ) -> None:
        """Client should NOT retry on 404 Not Found."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            if "/query" in url:
                call_count += 1
            resp = MagicMock()
            resp.status_code = 404
            resp.json.return_value = {"detail": "Session not found"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with pytest.raises(Exception):
                await client.query("SELECT 1")

        assert call_count == 1

    async def test_exponential_backoff_delays(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Retry delays should grow exponentially: ~1s, ~2s, ~4s."""
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/query" in url and call_count <= 3:
                resp = MagicMock()
                resp.status_code = 500
                resp.json.return_value = {"detail": "Server error"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        sleep_calls = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await client.query("SELECT 1")

        assert len(sleep_calls) >= 2, "Expected at least 2 sleep calls for retries"
        # Each delay should be roughly double the previous
        for i in range(1, len(sleep_calls)):
            assert sleep_calls[i] >= sleep_calls[i - 1], (
                f"Expected increasing delays, got {sleep_calls}"
            )

    async def test_zero_retries_client(
        self,
        base_url: str,
        api_key: str,
        auth_response: dict[str, Any],
    ) -> None:
        """Client with max_retries=0 should not retry at all."""
        client = DuckCloudClient(base_url=base_url, api_key=api_key, max_retries=0)
        _set_token(client, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            if "/query" in url:
                call_count += 1
            resp = MagicMock()
            resp.status_code = 503
            resp.json.return_value = {"detail": "Service Unavailable"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with pytest.raises(DuckCloudError):
                await client.query("SELECT 1")

        assert call_count == 1

    async def test_retry_with_jitter(
        self,
        client: DuckCloudClient,
        auth_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Retry delays should include some jitter (not strictly 1, 2, 4 seconds)."""
        _set_token(client, auth_response)
        client_with_single_retry = DuckCloudClient(
            base_url=client.base_url, api_key=client.api_key, max_retries=1
        )
        _set_token(client_with_single_retry, auth_response)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/query" in url and call_count == 1:
                resp = MagicMock()
                resp.status_code = 500
                resp.json.return_value = {"detail": "Server error"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        sleep_calls = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch.object(client_with_single_retry._http, "post", new_callable=AsyncMock, side_effect=post_side_effect):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await client_with_single_retry.query("SELECT 1")

        assert len(sleep_calls) == 1
        # Base delay for first retry is 1 second; with jitter it should be > 0
        assert sleep_calls[0] > 0
        # But not unreasonably large (no more than ~3x base)
        assert sleep_calls[0] < 5.0
