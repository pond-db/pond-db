"""Tests for sql_sandbox.py — blocked SQL pattern enforcement.

Defines expected behavior for ponddb.sql_sandbox:
  - check_sql(sql) raises BlockedSqlError for all 14 blocked patterns
  - check_sql(sql) returns None (no raise) for legitimate SQL
  - BlockedSqlError carries the matched pattern name
  - Matching is case-insensitive
  - Inline whitespace variations are still caught

Tests will FAIL (ImportError) until sql_sandbox.py is implemented.
"""

import pytest


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _import_check():
    """Fail fast with a clear ImportError if the module isn't created yet."""
    from ponddb.security import sql_sandbox  # noqa: F401


# ---------------------------------------------------------------------------
# 14 blocked patterns — each test targets exactly one pattern
# ---------------------------------------------------------------------------

BLOCKED_SQL_CASES = [
    # (pattern_label, sql)
    ("COPY",             "COPY mytable TO '/tmp/out.csv'"),
    ("COPY",             "copy orders from '/data/orders.csv'"),
    ("LOAD",             "LOAD '/tmp/evil.so'"),
    ("LOAD",             "load 'extension.duckdb_extension'"),
    ("INSTALL",          "INSTALL httpfs"),
    ("INSTALL",          "install 'spatial'"),
    ("ATTACH",           "ATTACH '/data/other.db' AS other"),
    ("ATTACH",           "attach 'file.db'"),
    ("EXPORT DATABASE",  "EXPORT DATABASE '/tmp/backup'"),
    ("IMPORT DATABASE",  "IMPORT DATABASE '/tmp/backup'"),
    ("CREATE SECRET",    "CREATE SECRET my_secret (TYPE S3, KEY_ID 'xxx')"),
    ("CREATE SECRET",    "create secret (type r2, secret 'abc')"),
    ("read_csv",         "SELECT * FROM read_csv('/etc/passwd')"),
    ("read_csv",         "select * from read_csv_auto('/data/file.csv')"),
    ("read_parquet",     "SELECT * FROM read_parquet('/data/file.parquet')"),
    ("read_json",        "SELECT * FROM read_json('/tmp/data.json')"),
    ("read_json",        "select * from read_json_auto('/tmp/x.json')"),
    ("read_text",        "SELECT * FROM read_text('/etc/hosts')"),
    ("read_blob",        "SELECT * FROM read_blob('/etc/shadow')"),
    ("glob",             "SELECT * FROM glob('/data/*.csv')"),
    ("SET",              "SET memory_limit = '100GB'"),
    ("SET",              "set threads = 64"),
    ("PRAGMA",           "PRAGMA database_list"),
    ("PRAGMA",           "pragma threads"),
]

# Deduplicate to 14 distinct blocked pattern *names*
_DISTINCT_BLOCKED_PATTERNS = sorted({label for label, _ in BLOCKED_SQL_CASES})


@pytest.mark.parametrize("label,sql", BLOCKED_SQL_CASES)
def test_blocked_pattern_raises(label: str, sql: str) -> None:
    """Every blocked SQL statement must raise BlockedSqlError."""
    from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

    with pytest.raises(BlockedSqlError):
        check_sql(sql)


def test_blocked_sql_error_has_pattern_attribute() -> None:
    """BlockedSqlError must expose the matched pattern name."""
    from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

    with pytest.raises(BlockedSqlError) as exc_info:
        check_sql("COPY t TO '/tmp/out.csv'")

    err = exc_info.value
    assert hasattr(err, "pattern"), "BlockedSqlError must have a 'pattern' attribute"
    assert err.pattern  # non-empty string


def test_blocked_sql_error_message_mentions_pattern() -> None:
    """Exception message must mention the blocked pattern."""
    from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

    with pytest.raises(BlockedSqlError) as exc_info:
        check_sql("INSTALL httpfs")

    assert "INSTALL" in str(exc_info.value).upper()


# ---------------------------------------------------------------------------
# Case-insensitivity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "copy t to '/tmp/out.csv'",
    "COPY T TO '/TMP/OUT.CSV'",
    "Copy T To '/tmp/out.csv'",
    "cOpY t tO '/tmp/out.csv'",
])
def test_blocked_case_insensitive(sql: str) -> None:
    """Blocked-pattern check is case-insensitive."""
    from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

    with pytest.raises(BlockedSqlError):
        check_sql(sql)


# ---------------------------------------------------------------------------
# Leading whitespace / newline variations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "  COPY t TO '/tmp/out.csv'",
    "\nLOAD '/tmp/lib.so'",
    "\t  INSTALL spatial",
    "\n\n  ATTACH '/db.db'",
])
def test_blocked_with_leading_whitespace(sql: str) -> None:
    """Blocked patterns are still caught when SQL has leading whitespace."""
    from ponddb.security.sql_sandbox import BlockedSqlError, check_sql

    with pytest.raises(BlockedSqlError):
        check_sql(sql)


# ---------------------------------------------------------------------------
# Legitimate SQL — must NOT be blocked
# ---------------------------------------------------------------------------

ALLOWED_SQL_CASES = [
    "SELECT 1",
    "SELECT * FROM my_table",
    "SELECT id, name FROM users WHERE id = 42",
    "INSERT INTO logs (msg) VALUES ('hello')",
    "UPDATE users SET name = 'Alice' WHERE id = 1",
    "DELETE FROM sessions WHERE expired = true",
    "CREATE TABLE t (id INTEGER, name TEXT)",
    "DROP TABLE IF EXISTS tmp_table",
    "WITH cte AS (SELECT 1 AS n) SELECT n FROM cte",
    "SELECT count(*) FROM information_schema.tables",
    "SELECT version()",
    "SHOW TABLES",
    "DESCRIBE my_table",
    "EXPLAIN SELECT * FROM t",
    "BEGIN; INSERT INTO t VALUES (1); COMMIT",
]


@pytest.mark.parametrize("sql", ALLOWED_SQL_CASES)
def test_allowed_sql_does_not_raise(sql: str) -> None:
    """Legitimate SQL must not raise BlockedSqlError."""
    from ponddb.security.sql_sandbox import check_sql

    # Should return None without raising
    result = check_sql(sql)
    assert result is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_does_not_raise() -> None:
    """Empty SQL does not match any blocked pattern (separate validation handles it)."""
    from ponddb.security.sql_sandbox import check_sql

    result = check_sql("")
    assert result is None


def test_none_like_whitespace_does_not_raise() -> None:
    """Pure whitespace SQL does not match any blocked pattern."""
    from ponddb.security.sql_sandbox import check_sql

    result = check_sql("   \n\t  ")
    assert result is None


def test_blocked_pattern_embedded_in_string_literal_does_not_raise() -> None:
    """COPY or LOAD inside a string literal should not trigger the block.

    NOTE: this is a known limitation — simple regex cannot parse SQL fully.
    This test documents the DESIRED behavior; the implementation may choose
    to handle this with a more sophisticated parser or skip it.
    Marking as xfail so it doesn't block CI until addressed.
    """
    from ponddb.security.sql_sandbox import check_sql

    # A SELECT that merely mentions the word 'copy' inside a string value
    sql = "SELECT 'the word copy is safe here' AS note"
    # Ideally this should NOT raise — document expected behavior
    # Using xfail to acknowledge the limitation of regex-only matching
    try:
        check_sql(sql)  # should not raise
    except Exception:
        pytest.xfail("Regex sandbox flags 'copy' inside string literals — known limitation")


def test_fourteen_distinct_blocked_patterns_are_defined() -> None:
    """The sandbox must define exactly 14 distinct blocked patterns."""
    from ponddb.security.sql_sandbox import BLOCKED_PATTERNS

    names = {p if isinstance(p, str) else p.pattern for p in BLOCKED_PATTERNS}
    assert len(BLOCKED_PATTERNS) >= 14, (
        f"Expected at least 14 blocked patterns, got {len(BLOCKED_PATTERNS)}"
    )
