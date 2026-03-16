"""Tests for subprocess_runner.py — isolated query execution via multiprocessing.

subprocess_runner.py wraps DuckDB query execution in a child process with
resource limits applied via setrlimit(RLIMIT_AS) and setrlimit(RLIMIT_CPU).
Results are passed back to the parent via multiprocessing.Queue.

NOTE on memory-limit tests:
DuckDB's mmap-based allocator reserves ~26 GB of virtual address space at
startup, so any finite RLIMIT_AS value triggers a C-level abort() in the
child.  When that child is a fork()-clone of pytest, the abort() writes to
inherited file descriptors and can corrupt pytest's internal state.
To prevent this, all memory-limit tests run their assertions inside a
subprocess.run() wrapper so the abort() is fully isolated from pytest.
"""

import json
import resource
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ponddb.session_manager import QueryResult

# Path used by subprocess helpers to find the ponddb package.
_SRC = str(Path(__file__).parent.parent / "src")


# ---------------------------------------------------------------------------
# Module-import guard — all tests fail cleanly if module not yet written
# ---------------------------------------------------------------------------


def _get_runner():
    """Import subprocess_runner lazily so collection always succeeds."""
    import importlib
    return importlib.import_module("ponddb.subprocess_runner")


def _run(sql: str, **kwargs) -> QueryResult:
    mod = _get_runner()
    return mod.run_query_isolated(sql, **kwargs)


# ---------------------------------------------------------------------------
# Subprocess helper for memory-limit tests
# ---------------------------------------------------------------------------


def _mem_subprocess(sql: str, memory_limit_bytes: int, timeout: int = 20) -> str:
    """Run run_query_isolated(sql, memory_limit_bytes=…) in an isolated process.

    Returns one of: 'ok', 'SubprocessMemoryError', 'SubprocessKilledError',
    or 'other:<ExcType>'.  Any C-level crash in the child is contained within
    this helper's subprocess; it cannot reach pytest.
    """
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {_SRC!r})
        from ponddb.subprocess_runner import (
            run_query_isolated,
            SubprocessMemoryError,
            SubprocessKilledError,
        )
        try:
            run_query_isolated({sql!r}, memory_limit_bytes={memory_limit_bytes})
            print("ok")
        except SubprocessMemoryError:
            print("SubprocessMemoryError")
        except SubprocessKilledError:
            print("SubprocessKilledError")
        except Exception as e:
            print(f"other:{{type(e).__name__}}")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout.strip() or f"crash(rc={proc.returncode})"


# ---------------------------------------------------------------------------
# Happy path — basic query execution
# ---------------------------------------------------------------------------


class TestRunQueryIsolatedHappyPath:
    """subprocess executes query and returns a QueryResult."""

    def test_module_importable(self):
        _get_runner()

    def test_run_query_isolated_callable(self):
        mod = _get_runner()
        assert callable(mod.run_query_isolated)

    def test_simple_select_returns_query_result(self):
        result = _run("SELECT 1 AS n")
        assert isinstance(result, QueryResult)

    def test_columns_populated(self):
        result = _run("SELECT 1 AS n")
        assert result.columns == ["n"]

    def test_rows_populated(self):
        result = _run("SELECT 1 AS n")
        assert result.rows == [[1]]

    def test_rowcount_correct(self):
        result = _run("SELECT 1 AS n")
        assert result.rowcount == 1

    def test_elapsed_ms_is_positive(self):
        result = _run("SELECT 1 AS n")
        assert result.elapsed_ms > 0

    def test_multi_row_query(self):
        result = _run("SELECT * FROM range(5) t(n)")
        assert result.rowcount == 5
        assert result.columns == ["n"]
        assert result.rows == [[0], [1], [2], [3], [4]]

    def test_multi_column_query(self):
        result = _run("SELECT 42 AS a, 'hello' AS b")
        assert result.columns == ["a", "b"]
        assert result.rows == [[42, "hello"]]

    def test_arithmetic_expression(self):
        result = _run("SELECT 2 + 2 AS sum")
        assert result.rows == [[4]]

    def test_string_functions(self):
        result = _run("SELECT upper('pond') AS u")
        assert result.rows == [["POND"]]

    def test_aggregate_query(self):
        result = _run("SELECT count(*) AS cnt FROM range(100) t(n)")
        assert result.rows == [[100]]

    def test_empty_result_set(self):
        result = _run("SELECT * FROM range(0) t(n)")
        assert result.rowcount == 0
        assert result.rows == []

    def test_two_calls_return_independent_results(self):
        r1 = _run("SELECT 1 AS x")
        r2 = _run("SELECT 2 AS x")
        assert r1.rows == [[1]]
        assert r2.rows == [[2]]


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Verify exception classes exist with the right inheritance."""

    def test_subprocess_runner_error_exists(self):
        mod = _get_runner()
        assert hasattr(mod, "SubprocessRunnerError")
        assert issubclass(mod.SubprocessRunnerError, Exception)

    def test_subprocess_killed_error_exists(self):
        mod = _get_runner()
        assert hasattr(mod, "SubprocessKilledError")
        assert issubclass(mod.SubprocessKilledError, mod.SubprocessRunnerError)

    def test_subprocess_memory_error_exists(self):
        mod = _get_runner()
        assert hasattr(mod, "SubprocessMemoryError")
        assert issubclass(mod.SubprocessMemoryError, mod.SubprocessKilledError)

    def test_subprocess_cpu_error_exists(self):
        mod = _get_runner()
        assert hasattr(mod, "SubprocessCpuError")
        assert issubclass(mod.SubprocessCpuError, mod.SubprocessKilledError)


# ---------------------------------------------------------------------------
# Result passing via Queue
# ---------------------------------------------------------------------------


class TestQueueResultPassing:
    """Result must travel from child → parent via multiprocessing.Queue."""

    def test_large_result_passes_through_queue(self):
        result = _run("SELECT n FROM range(1000) t(n)")
        assert result.rowcount == 1000
        assert result.rows[0] == [0]
        assert result.rows[999] == [999]

    def test_result_has_all_queryresult_fields(self):
        result = _run("SELECT 1 AS v")
        assert hasattr(result, "columns")
        assert hasattr(result, "rows")
        assert hasattr(result, "rowcount")
        assert hasattr(result, "elapsed_ms")

    def test_null_values_pass_through_queue(self):
        result = _run("SELECT NULL AS v")
        assert result.rows == [[None]]

    def test_float_values_pass_through_queue(self):
        result = _run("SELECT 3.14::DOUBLE AS pi")
        assert abs(float(result.rows[0][0]) - 3.14) < 1e-3

    def test_boolean_values_pass_through_queue(self):
        result = _run("SELECT true AS flag")
        assert result.rows[0][0] is True

    def test_invalid_sql_raises_exception(self):
        with pytest.raises(Exception):
            _run("SELECT * FROM nonexistent_table_xyz")

    def test_syntax_error_raises_exception(self):
        with pytest.raises(Exception):
            _run("NOT VALID SQL !!!!")


# ---------------------------------------------------------------------------
# Memory limit (RLIMIT_AS)
#
# These tests use _mem_subprocess() to keep DuckDB's C-level abort() fully
# isolated from pytest.  Any value of memory_limit_bytes < ~26 GB will kill
# DuckDB immediately because of its mmap-based virtual memory allocator;
# the tests verify that run_query_isolated surfaces this as SubprocessMemoryError
# or SubprocessKilledError.
# ---------------------------------------------------------------------------


class TestMemoryLimitEnforcement:
    """Child process killed when it exceeds memory limit."""

    def test_normal_query_without_memory_limit(self):
        # No memory limit → query succeeds normally.
        result = _run("SELECT 1 AS n", memory_limit_bytes=None)
        assert result.rowcount == 1

    def test_memory_overrun_raises_subprocess_killed_error(self):
        # 1 MB is far below DuckDB's virtual address space requirement (~26 GB).
        # The child is killed and the parent raises SubprocessMemoryError (a
        # subclass of SubprocessKilledError).
        outcome = _mem_subprocess(
            "SELECT a.n * b.n FROM range(10000) a(n), range(10000) b(n) LIMIT 1",
            memory_limit_bytes=1 * 1024 * 1024,
        )
        # Accept either SubprocessMemoryError or SubprocessKilledError —
        # both signal that the child was killed due to the memory limit.
        assert outcome in ("SubprocessMemoryError", "SubprocessKilledError"), (
            f"Expected a SubprocessKilledError variant, got: {outcome}"
        )

    def test_tight_memory_limit_kills_process(self):
        # Even a trivial query is killed when memory_limit_bytes is tiny.
        outcome = _mem_subprocess("SELECT 1 AS n", memory_limit_bytes=1)
        assert outcome in ("SubprocessMemoryError", "SubprocessKilledError"), (
            f"Expected a SubprocessKilledError variant, got: {outcome}"
        )

    def test_no_memory_limit_kwarg_works(self):
        result = _run("SELECT 1 AS n")
        assert result.rowcount == 1

    def test_memory_overrun_parent_survives(self):
        # After the isolated subprocess returns, the CURRENT pytest process
        # is still healthy and can run more queries.
        _mem_subprocess(
            "SELECT a.n FROM range(100000) a(n), range(100000) b(n)",
            memory_limit_bytes=1 * 1024 * 1024,
        )
        # Prove parent is alive by running another query successfully.
        result = _run("SELECT 99 AS alive")
        assert result.rows == [[99]]


# ---------------------------------------------------------------------------
# CPU time limit (RLIMIT_CPU)
# ---------------------------------------------------------------------------


class TestCpuTimeLimitEnforcement:
    """Child process killed (SIGXCPU) when it exceeds CPU time limit."""

    def test_fast_query_within_cpu_limit(self):
        result = _run("SELECT 1 AS n", cpu_time_seconds=5)
        assert result.rowcount == 1

    def test_cpu_intensive_query_exceeds_limit(self):
        mod = _get_runner()
        with pytest.raises(mod.SubprocessCpuError):
            # count(DISTINCT n) over 100 M rows forces a large hash table;
            # RLIMIT_CPU=1 (fires at 1 CPU-second) or the 10 s wall timeout
            # will kill the subprocess — either path raises SubprocessCpuError.
            _run(
                "SELECT count(DISTINCT n) FROM range(100000000) t(n)",
                cpu_time_seconds=1,
                timeout_seconds=10,
            )

    def test_no_cpu_limit_kwarg_works(self):
        result = _run("SELECT 1 AS n")
        assert result.rowcount == 1

    def test_wall_timeout_kills_hanging_subprocess(self):
        mod = _get_runner()
        with pytest.raises(mod.SubprocessCpuError):
            # sleep_ms(10000) blocks for 10 wall-clock seconds with near-zero
            # CPU usage.  The 2 s wall timeout kills the child.
            _run("SELECT sleep_ms(10000)", timeout_seconds=2.0)

    def test_cpu_overrun_parent_survives(self):
        mod = _get_runner()
        caught = False
        try:
            _run(
                "SELECT count(DISTINCT n) FROM range(100000000) t(n)",
                cpu_time_seconds=1,
                timeout_seconds=10,
            )
        except mod.SubprocessKilledError:
            caught = True
        assert caught, "Expected SubprocessKilledError (or subclass)"
        # Parent is still healthy after child was killed.
        result = _run("SELECT 42 AS alive")
        assert result.rows == [[42]]


# ---------------------------------------------------------------------------
# POND_SUBPROCESS_ISOLATION integration with SessionManager
# ---------------------------------------------------------------------------


class TestSessionManagerSubprocessIntegration:
    """execute_query uses subprocess isolation when POND_SUBPROCESS_ISOLATION=true."""

    def test_execute_query_calls_run_query_isolated_when_env_true(self, monkeypatch):
        monkeypatch.setenv("POND_SUBPROCESS_ISOLATION", "true")
        mod = _get_runner()
        calls: list[str] = []
        original = mod.run_query_isolated

        def spy(sql: str, **kwargs):
            calls.append(sql)
            return original(sql, **kwargs)

        monkeypatch.setattr(mod, "run_query_isolated", spy)

        import importlib
        import ponddb.session_manager as sm_mod
        importlib.reload(sm_mod)

        mgr = sm_mod.SessionManager()
        sid = mgr.create_session()
        result = mgr.execute_query(sid, "SELECT 1 AS n")
        mgr.destroy_session(sid)

        assert len(calls) >= 1
        assert result.rows == [[1]]

    def test_execute_query_skips_subprocess_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("POND_SUBPROCESS_ISOLATION", raising=False)
        mod = _get_runner()
        calls: list[str] = []

        def spy(sql: str, **kwargs):
            calls.append(sql)

        monkeypatch.setattr(mod, "run_query_isolated", spy)

        import ponddb.session_manager as sm_mod
        mgr = sm_mod.SessionManager()
        sid = mgr.create_session()
        result = mgr.execute_query(sid, "SELECT 1 AS n")
        mgr.destroy_session(sid)

        assert len(calls) == 0
        assert result.rows == [[1]]

    def test_execute_query_skips_subprocess_when_env_false(self, monkeypatch):
        monkeypatch.setenv("POND_SUBPROCESS_ISOLATION", "false")
        mod = _get_runner()
        calls: list[str] = []

        def spy(sql: str, **kwargs):
            calls.append(sql)

        monkeypatch.setattr(mod, "run_query_isolated", spy)

        import ponddb.session_manager as sm_mod
        mgr = sm_mod.SessionManager()
        sid = mgr.create_session()
        result = mgr.execute_query(sid, "SELECT 42 AS v")
        mgr.destroy_session(sid)

        assert len(calls) == 0
        assert result.rows == [[42]]

    def test_subprocess_isolation_returns_correct_result(self, monkeypatch):
        monkeypatch.setenv("POND_SUBPROCESS_ISOLATION", "true")

        import ponddb.session_manager as sm_mod
        mgr = sm_mod.SessionManager()
        sid = mgr.create_session()
        result = mgr.execute_query(sid, "SELECT 7 * 6 AS answer")
        mgr.destroy_session(sid)

        assert result.columns == ["answer"]
        assert result.rows == [[42]]
        assert result.rowcount == 1

    def test_subprocess_query_error_propagates_via_execute_query(self, monkeypatch):
        monkeypatch.setenv("POND_SUBPROCESS_ISOLATION", "true")

        import ponddb.session_manager as sm_mod
        mgr = sm_mod.SessionManager()
        sid = mgr.create_session()
        with pytest.raises(Exception):
            mgr.execute_query(sid, "SELECT * FROM no_such_table_abc")
        mgr.destroy_session(sid)


# ---------------------------------------------------------------------------
# Process isolation guarantees
# ---------------------------------------------------------------------------


class TestProcessIsolation:
    """Each call runs in a separate child process — state cannot bleed across calls."""

    def test_two_calls_do_not_share_in_memory_tables(self):
        """CREATE TABLE in one subprocess must not be visible in the next."""
        _run("CREATE TABLE temp_isolation_test (x INT)")
        with pytest.raises(Exception):
            _run("SELECT * FROM temp_isolation_test")

    def test_setrlimit_does_not_affect_parent_rlimit_as(self):
        """RLIMIT_AS applied in child must not change parent limits."""
        soft_before, hard_before = resource.getrlimit(resource.RLIMIT_AS)

        _run("SELECT 1 AS n", memory_limit_bytes=None)

        soft_after, hard_after = resource.getrlimit(resource.RLIMIT_AS)
        assert soft_before == soft_after
        assert hard_before == hard_after

    def test_setrlimit_does_not_affect_parent_rlimit_cpu(self):
        """RLIMIT_CPU applied in child must not change parent limits."""
        soft_before, hard_before = resource.getrlimit(resource.RLIMIT_CPU)

        _run("SELECT 1 AS n", cpu_time_seconds=5)

        soft_after, hard_after = resource.getrlimit(resource.RLIMIT_CPU)
        assert soft_before == soft_after
        assert hard_before == hard_after

    def test_child_process_terminates_after_query(self):
        """No zombie processes accumulate after successful queries."""
        import multiprocessing
        before = len(multiprocessing.active_children())
        _run("SELECT 1 AS n")
        time.sleep(0.1)
        after = len(multiprocessing.active_children())
        assert after <= before
