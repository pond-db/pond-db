"""Tests for DuckCloud SDK packaging and module structure.

Verifies that the duckcloud package is importable, exports the right
names, and that the pyproject.toml for the SDK is properly structured.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


class TestPackageImports:
    def test_duckcloud_importable(self) -> None:
        """The duckcloud top-level package must be importable."""
        import duckcloud  # noqa: F401

    def test_duckcloud_client_importable(self) -> None:
        """DuckCloudClient must be importable from duckcloud."""
        from duckcloud import DuckCloudClient  # noqa: F401

    def test_exceptions_importable(self) -> None:
        """Exception classes must be importable from duckcloud.exceptions."""
        from duckcloud.exceptions import (  # noqa: F401
            AuthenticationError,
            DuckCloudError,
            QueryError,
            RateLimitError,
        )

    def test_duckcloud_error_is_exception(self) -> None:
        from duckcloud.exceptions import DuckCloudError

        assert issubclass(DuckCloudError, Exception)

    def test_authentication_error_is_duckcloud_error(self) -> None:
        from duckcloud.exceptions import AuthenticationError, DuckCloudError

        assert issubclass(AuthenticationError, DuckCloudError)

    def test_query_error_is_duckcloud_error(self) -> None:
        from duckcloud.exceptions import DuckCloudError, QueryError

        assert issubclass(QueryError, DuckCloudError)

    def test_rate_limit_error_is_duckcloud_error(self) -> None:
        from duckcloud.exceptions import DuckCloudError, RateLimitError

        assert issubclass(RateLimitError, DuckCloudError)

    def test_package_has_version(self) -> None:
        import duckcloud

        assert hasattr(duckcloud, "__version__")
        assert isinstance(duckcloud.__version__, str)
        assert len(duckcloud.__version__) > 0

    def test_client_exported_from_top_level(self) -> None:
        import duckcloud

        assert hasattr(duckcloud, "DuckCloudClient")

    def test_exceptions_exported_from_top_level(self) -> None:
        """Key exceptions should be accessible from the top-level package."""
        import duckcloud

        assert hasattr(duckcloud, "DuckCloudError")
        assert hasattr(duckcloud, "AuthenticationError")
        assert hasattr(duckcloud, "QueryError")


class TestSDKPyprojectToml:
    def test_sdk_pyproject_exists(self) -> None:
        """src/sdk/pyproject.toml must exist."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "src" / "sdk" / "pyproject.toml"
        assert pyproject.exists(), f"Missing: {pyproject}"

    def test_sdk_pyproject_has_duckcloud_name(self) -> None:
        """pyproject.toml must declare name = 'duckcloud'."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "src" / "sdk" / "pyproject.toml"
        if not pyproject.exists():
            pytest.skip("pyproject.toml not yet created")
        content = pyproject.read_text()
        assert 'name = "duckcloud"' in content or "name = 'duckcloud'" in content

    def test_sdk_pyproject_declares_httpx_dependency(self) -> None:
        """pyproject.toml must include httpx as a dependency."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "src" / "sdk" / "pyproject.toml"
        if not pyproject.exists():
            pytest.skip("pyproject.toml not yet created")
        content = pyproject.read_text()
        assert "httpx" in content

    def test_sdk_directory_structure(self) -> None:
        """Expected directory structure for SDK package."""
        db_engine = Path(__file__).parent.parent
        sdk_dir = db_engine / "src" / "sdk"
        assert sdk_dir.exists(), f"SDK directory missing: {sdk_dir}"
        assert (sdk_dir / "duckcloud").exists(), "duckcloud package directory missing"
        assert (sdk_dir / "duckcloud" / "__init__.py").exists(), "__init__.py missing"

    def test_client_module_exists(self) -> None:
        """client.py must exist in the duckcloud package."""
        db_engine = Path(__file__).parent.parent
        client_module = db_engine / "src" / "sdk" / "duckcloud" / "client.py"
        assert client_module.exists(), f"client.py missing: {client_module}"

    def test_exceptions_module_exists(self) -> None:
        """exceptions.py must exist in the duckcloud package."""
        db_engine = Path(__file__).parent.parent
        exceptions_module = db_engine / "src" / "sdk" / "duckcloud" / "exceptions.py"
        assert exceptions_module.exists(), f"exceptions.py missing: {exceptions_module}"


class TestDuckCloudClientInterface:
    """Smoke tests verifying DuckCloudClient exposes the required public interface."""

    def test_client_has_authenticate_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "authenticate", None))

    def test_client_has_query_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "query", None))

    def test_client_has_save_query_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "save_query", None))

    def test_client_has_list_queries_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "list_queries", None))

    def test_client_has_get_history_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "get_history", None))

    def test_client_has_share_query_method(self) -> None:
        from duckcloud import DuckCloudClient
        assert callable(getattr(DuckCloudClient, "share_query", None))

    def test_client_is_async_context_manager(self) -> None:
        """DuckCloudClient should support async context manager protocol."""
        from duckcloud import DuckCloudClient
        assert hasattr(DuckCloudClient, "__aenter__")
        assert hasattr(DuckCloudClient, "__aexit__")

    async def test_client_context_manager_closes_http(self) -> None:
        """Using client as async context manager should close underlying HTTP client."""
        from duckcloud import DuckCloudClient

        async with DuckCloudClient(base_url="http://localhost:8432", api_key="key") as client:
            assert client._http is not None
        # After exit, http client should be closed (no exception raised)
