# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Token revocation blocklist for PondDB JWT tokens.

Uses an in-memory set as primary storage. If a Redis-backed implementation
is added later, is_revoked should raise on Redis failure so callers can
fail open (allow) and log a warning.
"""

import logging
from typing import Set

logger: logging.Logger = logging.getLogger("ponddb.auth.token_blocklist")

# In-memory blocklist — persists for the lifetime of the process.
_blocklist: Set[str] = set()


def add_to_blocklist(jti: str) -> None:
    """Add a JWT ID to the revocation blocklist."""
    _blocklist.add(jti)


def is_revoked(jti: str) -> bool:
    """Return True if the given jti has been revoked.

    Raises an exception if the underlying storage is unavailable so that
    callers can fail open and log a warning.
    """
    return jti in _blocklist


def remove_from_blocklist(jti: str) -> None:
    """Remove a JWT ID from the blocklist (used for test cleanup)."""
    _blocklist.discard(jti)
