# Copyright (c) 2026 Tianlu
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""DuckCloud SDK exception hierarchy."""


class DuckCloudError(Exception):
    """Base exception for all DuckCloud SDK errors."""


class AuthenticationError(DuckCloudError):
    """Raised when authentication fails or token cannot be refreshed."""


class QueryError(DuckCloudError):
    """Raised when a SQL query fails (e.g. syntax error, bad request)."""


class RateLimitError(DuckCloudError):
    """Raised when the server responds with 429 Too Many Requests."""
