# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""PondDB SDK exception hierarchy."""


class PondDBError(Exception):
    """Base exception for all PondDB SDK errors."""


class AuthenticationError(PondDBError):
    """Raised when authentication fails or token cannot be refreshed."""


class QueryError(PondDBError):
    """Raised when a SQL query fails (e.g. syntax error, bad request)."""


class RateLimitError(PondDBError):
    """Raised when the server responds with 429 Too Many Requests."""
