"""subprocess_runner.py — isolated DuckDB query execution via multiprocessing.

Runs each query in a fresh child process with RLIMIT_AS and RLIMIT_CPU
applied before any DuckDB work. Results travel back to the parent via
a multiprocessing.Queue.
"""

import multiprocessing
import os
import resource
import signal
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ponddb.session_manager import QueryResult


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class SubprocessRunnerError(Exception):
    """Base exception for all subprocess runner errors."""


class SubprocessKilledError(SubprocessRunnerError):
    """Child process was killed before completing."""


class SubprocessMemoryError(SubprocessKilledError):
    """Child process killed due to exceeding RLIMIT_AS (virtual memory)."""


class SubprocessCpuError(SubprocessKilledError):
    """Child process killed due to exceeding RLIMIT_CPU (CPU time)."""


# ---------------------------------------------------------------------------
# Child-process worker
# ---------------------------------------------------------------------------


def _worker(
    sql: str,
    result_queue: multiprocessing.Queue,
    memory_limit_bytes: Optional[int],
    cpu_time_seconds: Optional[int],
) -> None:
    """Entry point executed in the child process."""
    import duckdb  # imported here to keep parent import-time clean
    from ponddb.session_manager import QueryResult  # safe in child process

    try:
        # Apply resource limits before any heavy allocation.
        if memory_limit_bytes is not None:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_limit_bytes, memory_limit_bytes),
            )
        if cpu_time_seconds is not None:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (cpu_time_seconds, cpu_time_seconds),
            )

        start = time.perf_counter()
        conn = duckdb.connect(":memory:")
        rel = conn.execute(sql)

        if rel is None:
            columns: list[str] = []
            rows: list[list] = []
        else:
            columns = [desc[0] for desc in rel.description] if rel.description else []
            rows = [list(r) for r in rel.fetchall()]

        elapsed_ms = (time.perf_counter() - start) * 1000
        conn.close()

        result = QueryResult(
            columns=columns,
            rows=rows,
            rowcount=len(rows),
            elapsed_ms=elapsed_ms,
        )
        result_queue.put(("ok", result))

    except Exception as exc:  # noqa: BLE001
        try:
            result_queue.put(("error", type(exc).__name__, str(exc)))
        except Exception:  # noqa: BLE001
            # queue.put itself can fail under extreme memory pressure;
            # parent will detect via exit code.
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_query_isolated(
    sql: str,
    memory_limit_bytes: Optional[int] = None,
    cpu_time_seconds: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> "QueryResult":
    """Execute *sql* in an isolated child process and return a QueryResult.

    Parameters
    ----------
    sql:
        SQL statement to execute.
    memory_limit_bytes:
        If given, sets RLIMIT_AS in the child to this value. Exceeding the
        limit raises SubprocessMemoryError in the caller.
    cpu_time_seconds:
        If given, sets RLIMIT_CPU (seconds of CPU time) in the child.
        Exceeding the limit raises SubprocessCpuError in the caller.
    timeout_seconds:
        Wall-clock timeout for the child. If the child is still alive after
        this many seconds the parent sends SIGKILL and raises SubprocessCpuError.
        Defaults to cpu_time_seconds + 5 when cpu_time_seconds is set, or None.
    """
    result_queue: multiprocessing.Queue = multiprocessing.Queue()

    p = multiprocessing.Process(
        target=_worker,
        args=(sql, result_queue, memory_limit_bytes, cpu_time_seconds),
        daemon=True,
    )
    p.start()

    # Decide wall-clock timeout for join.
    wall_timeout: Optional[float] = timeout_seconds
    if cpu_time_seconds is not None and wall_timeout is None:
        wall_timeout = float(cpu_time_seconds) + 5.0

    timed_out = False
    p.join(timeout=wall_timeout)

    if p.is_alive():
        timed_out = True
        p.kill()
        p.join(timeout=5.0)

    exitcode = p.exitcode

    # --- Try to read whatever the child put in the queue ---
    item = None
    try:
        if not result_queue.empty():
            item = result_queue.get_nowait()
    except Exception:  # noqa: BLE001
        item = None

    if item is not None:
        if item[0] == "ok":
            return item[1]
        # item == ("error", exc_name, exc_str)
        raise Exception(f"{item[1]}: {item[2]}")

    # --- Child died without putting anything in the queue ---
    if timed_out:
        raise SubprocessCpuError(
            f"Process exceeded wall timeout of {timeout_seconds}s"
        )

    if exitcode == -signal.SIGXCPU:
        raise SubprocessCpuError(
            f"Process exceeded CPU time limit of {cpu_time_seconds}s"
        )

    if memory_limit_bytes is not None:
        raise SubprocessMemoryError(
            f"Process exceeded memory limit of {memory_limit_bytes} bytes "
            f"(exit code {exitcode})"
        )

    if cpu_time_seconds is not None:
        raise SubprocessCpuError(
            f"Process exceeded CPU time limit of {cpu_time_seconds}s "
            f"(exit code {exitcode})"
        )

    raise SubprocessKilledError(
        f"Process was killed unexpectedly (exit code {exitcode})"
    )
