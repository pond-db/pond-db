# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""SQL sandbox — blocked pattern enforcement for PondDB query endpoint."""

import re


class BlockedSqlError(Exception):
    """Raised when SQL matches a blocked pattern."""

    def __init__(self, pattern: str, sql: str) -> None:
        self.pattern = pattern
        super().__init__(f"SQL blocked: pattern '{pattern}' is not allowed")


# (human-readable name, regex pattern string)
_PATTERN_DEFS: list[tuple[str, str]] = [
    ("COPY",            r"^\s*copy\b"),
    ("LOAD",            r"^\s*load\b"),
    ("INSTALL",         r"^\s*install\b"),
    ("ATTACH",          r"^\s*attach\b"),
    ("EXPORT DATABASE", r"^\s*export\s+database\b"),
    ("IMPORT DATABASE", r"^\s*import\s+database\b"),
    ("CREATE SECRET",   r"^\s*create\s+secret\b"),
    ("SET",             r"^\s*set\b"),
    ("PRAGMA",          r"^\s*pragma\b"),
    ("read_csv",        r"\bread_csv"),
    ("read_parquet",    r"\bread_parquet\b"),
    ("read_json",       r"\bread_json"),
    ("read_text",       r"\bread_text\b"),
    ("read_blob",       r"\bread_blob\b"),
    ("glob",            r"\bglob\s*\("),
]

# Compiled patterns exposed as BLOCKED_PATTERNS (each has a .pattern attribute)
BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(pat, re.IGNORECASE) for _, pat in _PATTERN_DEFS
]

_PATTERN_NAMES: list[str] = [name for name, _ in _PATTERN_DEFS]


def check_sql(sql: str) -> None:
    """Check SQL for blocked patterns.

    Returns None if the SQL is allowed.
    Raises BlockedSqlError if the SQL matches a blocked pattern.
    """
    if not sql or not sql.strip():
        return None

    for name, regex in zip(_PATTERN_NAMES, BLOCKED_PATTERNS):
        if regex.search(sql):
            raise BlockedSqlError(name, sql)

    return None
