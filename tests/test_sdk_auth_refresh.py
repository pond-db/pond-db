"""Tests for PondDB SDK JWT token auto-refresh behavior.

Tests that the client transparently refreshes its access token
when it expires or receives a 401, without interrupting the caller.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ponddb.client import PondClient
from ponddb.exceptions import AuthenticationError


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
        "access_token": "eyJhbGciOiJIUzI1NiJ9.access.v1",
        "refresh_token": "eyJhbGciOiJIUzI1NiJ9.refresh.v1",
        "token_type": "bearer",
        "expires_in": 3600,
    }


@pytest.fixture
def refreshed_token_response() -> dict[str, Any]:
    return {
        "access_token": "eyJhbGciOiJIUzI1NiJ9.access.v2",
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


class TestTokenRefresh:
    def _set_token(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        age_seconds: float = 0.0,
    ) -> None:
        client.access_token = auth_response["access_token"]
        client.refresh_token = auth_response["refresh_token"]
        client.expires_in = auth_response["expires_in"]
        client._token_acquired_at = time.monotonic() - age_seconds

    async def test_token_refresh_on_401(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        refreshed_token_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """When server returns 401, client should refresh and retry automatically."""
        self._set_token(client, auth_response)

        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "/auth/refresh" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = refreshed_token_response
                resp.raise_for_status = MagicMock()
                return resp
            if call_count == 1:
                # First query attempt fails with 401
                resp = MagicMock()
                resp.status_code = 401
                resp.json.return_value = {"detail": "Token expired"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "401", request=MagicMock(), response=resp
                )
                return resp
            # Retry after refresh succeeds
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            result = await client.query("SELECT 1")

        assert result["rowcount"] == 1
        assert client.access_token == refreshed_token_response["access_token"]

    async def test_token_refresh_uses_refresh_endpoint(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        refreshed_token_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Token refresh must call POST /auth/refresh with the refresh_token."""
        self._set_token(client, auth_response)
        refresh_calls = []

        async def post_side_effect(url, **kwargs):
            if "/auth/refresh" in url:
                refresh_calls.append({"url": url, "kwargs": kwargs})
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = refreshed_token_response
                resp.raise_for_status = MagicMock()
                return resp
            if not refresh_calls:
                resp = MagicMock()
                resp.status_code = 401
                resp.json.return_value = {"detail": "Token expired"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "401", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            await client.query("SELECT 1")

        assert len(refresh_calls) == 1
        body = refresh_calls[0]["kwargs"].get("json", {})
        assert body.get("refresh_token") == auth_response["refresh_token"]

    async def test_proactive_refresh_before_expiry(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        refreshed_token_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """Client should proactively refresh token when it's near expiry."""
        # Set token acquired 3500 seconds ago (100s from expiry on a 3600s token)
        self._set_token(client, auth_response, age_seconds=3500)
        # expires_in is 3600, so token has ~100s left — should proactively refresh

        refresh_called = []

        async def post_side_effect(url, **kwargs):
            if "/auth/refresh" in url:
                refresh_called.append(True)
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = refreshed_token_response
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            await client.query("SELECT 1")

        # Should have proactively refreshed
        assert len(refresh_called) >= 1

    async def test_raises_auth_error_if_refresh_fails(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        """If refresh token is also expired, client raises AuthenticationError."""
        self._set_token(client, auth_response)

        async def post_side_effect(url, **kwargs):
            if "/auth/refresh" in url:
                resp = MagicMock()
                resp.status_code = 401
                resp.json.return_value = {"detail": "Refresh token expired"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "401", request=MagicMock(), response=resp
                )
                return resp
            # All query calls fail with 401
            resp = MagicMock()
            resp.status_code = 401
            resp.json.return_value = {"detail": "Token expired"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            with pytest.raises(AuthenticationError):
                await client.query("SELECT 1")

    async def test_no_refresh_on_non_auth_errors(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
    ) -> None:
        """400/500 errors should NOT trigger token refresh."""
        self._set_token(client, auth_response)
        refresh_calls = []

        async def post_side_effect(url, **kwargs):
            if "/auth/refresh" in url:
                refresh_calls.append(True)
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 400
            resp.json.return_value = {"detail": "Bad SQL"}
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400", request=MagicMock(), response=resp
            )
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            with pytest.raises(Exception):
                await client.query("INVALID SQL")

        assert len(refresh_calls) == 0

    async def test_refresh_token_updated_after_refresh(
        self,
        client: PondClient,
        auth_response: dict[str, Any],
        refreshed_token_response: dict[str, Any],
        query_result: dict[str, Any],
    ) -> None:
        """After a successful token refresh, the new access_token is stored."""
        self._set_token(client, auth_response)
        original_token = client.access_token

        async def post_side_effect(url, **kwargs):
            if "/auth/refresh" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = refreshed_token_response
                resp.raise_for_status = MagicMock()
                return resp
            if client.access_token == original_token:
                resp = MagicMock()
                resp.status_code = 401
                resp.json.return_value = {"detail": "Token expired"}
                resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                    "401", request=MagicMock(), response=resp
                )
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = query_result
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(
            client._http, "post", new_callable=AsyncMock, side_effect=post_side_effect
        ):
            await client.query("SELECT 1")

        assert client.access_token == refreshed_token_response["access_token"]
        assert client.access_token != original_token
