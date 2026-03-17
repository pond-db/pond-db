# Copyright (c) 2026 DatabaseCompany
# Licensed under the Business Source License 1.1
# See LICENSE file for details

"""Custom CORS middleware with allowlist semantics — no wildcard, 204 for preflight."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class AllowlistCORSMiddleware(BaseHTTPMiddleware):
    """CORS middleware that echoes allowed origins (never wildcard).

    - Allowed origin on simple request → ACAO echoes origin.
    - Disallowed origin on simple request → no ACAO header.
    - Preflight (OPTIONS + Access-Control-Request-Method) from allowed origin → 204 + ACAO + ACAM.
    - Preflight from disallowed origin → 400.
    """

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        super().__init__(app)
        self._allow_origins: frozenset[str] = frozenset(allow_origins)

    async def dispatch(self, request: Request, call_next: object) -> Response:
        origin = request.headers.get("origin", "")
        is_preflight = (
            request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
        )

        if is_preflight:
            if origin in self._allow_origins:
                return Response(
                    status_code=204,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Max-Age": "600",
                    },
                )
            # Disallowed origin → reject preflight (no ACAO)
            return Response(status_code=400)

        # Simple (non-preflight) request
        response: Response = await call_next(request)  # type: ignore[operator]
        if origin and origin in self._allow_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
        return response
