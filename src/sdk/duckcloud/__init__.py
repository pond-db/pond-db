# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""DuckCloud Python SDK."""

from .client import DuckCloudClient
from .exceptions import AuthenticationError, DuckCloudError, QueryError, RateLimitError

__version__ = "0.1.0"

__all__ = [
    "DuckCloudClient",
    "DuckCloudError",
    "AuthenticationError",
    "QueryError",
    "RateLimitError",
]
