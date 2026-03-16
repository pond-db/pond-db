"""SecurityHeadersMiddleware — injects security headers on every response."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response.

    In dev_mode (default False) HSTS is omitted — useful for local HTTP development.
    """

    def __init__(self, app, dev_mode: bool = False) -> None:
        super().__init__(app)
        self._dev_mode = dev_mode

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            response: Response = await call_next(request)
        except Exception:
            from starlette.responses import PlainTextResponse
            response = PlainTextResponse("Internal Server Error", status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://esm.sh; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        if not self._dev_mode:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response
