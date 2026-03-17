# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""PondDB — lightweight self-hosted DuckDB compute platform."""

from ponddb.client import PondClient, PondDB
from ponddb.exceptions import AuthenticationError, PondDBError, QueryError, RateLimitError

__version__ = "1.0.0"
__all__ = [
    "PondClient",
    "PondDB",
    "PondDBError",
    "AuthenticationError",
    "QueryError",
    "RateLimitError",
    "__version__",
]
