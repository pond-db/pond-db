"""Tests for PondDB SDK packaging and module structure.

Verifies that the ponddb package is importable, exports the right
names, and that the SDK client is properly structured.

Also tests backward compatibility with the legacy duckcloud SDK package.
"""

from pathlib import Path


class TestPackageImports:
    def test_ponddb_importable(self) -> None:
        """The ponddb top-level package must be importable."""
        import ponddb  # noqa: F401

    def test_pond_client_importable(self) -> None:
        """PondClient must be importable from ponddb."""
        from ponddb import PondClient  # noqa: F401

    def test_pond_db_importable(self) -> None:
        """PondDB (alias) must be importable from ponddb."""
        from ponddb import PondDB  # noqa: F401

    def test_exceptions_importable(self) -> None:
        """Exception classes must be importable from ponddb.exceptions."""
        from ponddb.exceptions import (  # noqa: F401
            AuthenticationError,
            PondDBError,
            QueryError,
            RateLimitError,
        )

    def test_ponddb_error_is_exception(self) -> None:
        from ponddb.exceptions import PondDBError

        assert issubclass(PondDBError, Exception)

    def test_authentication_error_is_ponddb_error(self) -> None:
        from ponddb.exceptions import AuthenticationError, PondDBError

        assert issubclass(AuthenticationError, PondDBError)

    def test_query_error_is_ponddb_error(self) -> None:
        from ponddb.exceptions import PondDBError, QueryError

        assert issubclass(QueryError, PondDBError)

    def test_rate_limit_error_is_ponddb_error(self) -> None:
        from ponddb.exceptions import PondDBError, RateLimitError

        assert issubclass(RateLimitError, PondDBError)

    def test_package_has_version(self) -> None:
        import ponddb

        assert hasattr(ponddb, "__version__")
        assert isinstance(ponddb.__version__, str)
        assert len(ponddb.__version__) > 0

    def test_client_exported_from_top_level(self) -> None:
        import ponddb

        assert hasattr(ponddb, "PondClient")

    def test_exceptions_exported_from_top_level(self) -> None:
        """Key exceptions should be accessible from the top-level package."""
        import ponddb

        assert hasattr(ponddb, "PondDBError")
        assert hasattr(ponddb, "AuthenticationError")
        assert hasattr(ponddb, "QueryError")


class TestSDKPyprojectToml:
    def test_main_pyproject_exists(self) -> None:
        """pyproject.toml must exist at repo root."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "pyproject.toml"
        assert pyproject.exists(), f"Missing: {pyproject}"

    def test_main_pyproject_has_ponddb_name(self) -> None:
        """pyproject.toml must declare name = 'ponddb'."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "pyproject.toml"
        content = pyproject.read_text()
        assert 'name = "ponddb"' in content or "name = 'ponddb'" in content

    def test_main_pyproject_declares_httpx_dependency(self) -> None:
        """pyproject.toml must include httpx as a dependency."""
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "pyproject.toml"
        content = pyproject.read_text()
        assert "httpx" in content

    def test_client_module_exists(self) -> None:
        """client.py must exist in the ponddb package."""
        db_engine = Path(__file__).parent.parent
        client_module = db_engine / "src" / "ponddb" / "client.py"
        assert client_module.exists(), f"client.py missing: {client_module}"

    def test_exceptions_module_exists(self) -> None:
        """exceptions.py must exist in the ponddb package."""
        db_engine = Path(__file__).parent.parent
        exceptions_module = db_engine / "src" / "ponddb" / "exceptions.py"
        assert exceptions_module.exists(), f"exceptions.py missing: {exceptions_module}"


class TestLegacySDKCompatibility:
    """Tests that the legacy duckcloud SDK package still exists and works."""

    def test_legacy_sdk_directory_exists(self) -> None:
        db_engine = Path(__file__).parent.parent
        sdk_dir = db_engine / "src" / "sdk"
        assert sdk_dir.exists(), f"SDK directory missing: {sdk_dir}"
        assert (sdk_dir / "duckcloud").exists(), "duckcloud package directory missing"
        assert (sdk_dir / "duckcloud" / "__init__.py").exists(), "__init__.py missing"

    def test_legacy_duckcloud_importable(self) -> None:
        """The legacy duckcloud package must still be importable."""
        import duckcloud  # noqa: F401

    def test_legacy_client_importable(self) -> None:
        """DuckCloudClient must still be importable for backward compatibility."""
        from duckcloud import DuckCloudClient  # noqa: F401

    def test_legacy_pyproject_exists(self) -> None:
        db_engine = Path(__file__).parent.parent
        pyproject = db_engine / "src" / "sdk" / "pyproject.toml"
        assert pyproject.exists(), f"Missing: {pyproject}"


class TestPondClientInterface:
    """Smoke tests verifying PondClient exposes the required public interface."""

    def test_client_has_authenticate_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "authenticate", None))

    def test_client_has_query_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "query", None))

    def test_client_has_save_query_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "save_query", None))

    def test_client_has_list_queries_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "list_queries", None))

    def test_client_has_get_history_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "get_history", None))

    def test_client_has_share_query_method(self) -> None:
        from ponddb import PondClient
        assert callable(getattr(PondClient, "share_query", None))

    def test_client_is_async_context_manager(self) -> None:
        """PondClient should support async context manager protocol."""
        from ponddb import PondClient
        assert hasattr(PondClient, "__aenter__")
        assert hasattr(PondClient, "__aexit__")

    async def test_client_context_manager_closes_http(self) -> None:
        """Using client as async context manager should close underlying HTTP client."""
        from ponddb import PondClient

        async with PondClient(base_url="http://localhost:8432", api_key="key") as client:
            assert client._http is not None
