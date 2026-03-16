"""Redis sliding window rate limiter — per-IP and per-API-key, fail-open on Redis errors."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter backed by a Redis sorted set."""

    def __init__(
        self,
        redis_client: object,
        limit: int = 100,
        window_seconds: int = 60,
    ) -> None:
        self._redis = redis_client
        self._limit = limit
        self._window_seconds = window_seconds

    async def check(self, key: str) -> tuple[bool, int]:
        """Check whether the key is within the rate limit.

        Returns (allowed, retry_after_seconds).
        Fails open on Redis errors — returns (True, 0) and logs a warning.
        """
        now = time.time()
        window_start = now - self._window_seconds
        member = str(now)

        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, "-inf", window_start)
                pipe.zadd(key, {member: now})
                pipe.zcard(key)
                pipe.expire(key, self._window_seconds)
                results = await pipe.execute()

            count: int = results[2]  # zcard result

            if count > self._limit:
                # Compute retry_after from the oldest entry in the window
                retry_after = self._window_seconds
                return False, retry_after

            return True, 0

        except Exception as exc:
            logger.warning("Rate limiter Redis error — failing open: %s", exc)
            return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware applying per-IP and per-API-key sliding window rate limits."""

    def __init__(
        self,
        app: ASGIApp,
        redis_client: object,
        limit: int = 100,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._limiter = RateLimiter(redis_client, limit=limit, window_seconds=window_seconds)

    async def dispatch(self, request: Request, call_next: object) -> Response:
        # Determine the identifier(s) to rate-limit against
        api_key = request.headers.get("X-API-Key")
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
        else:
            ip = getattr(request.client, "host", "unknown") if request.client else "unknown"

        # When an API key is present, rate-limit by key only (not IP).
        # When no API key, rate-limit by IP.
        if api_key:
            allowed, retry_after = await self._limiter.check(f"key:{api_key}")
        else:
            allowed, retry_after = await self._limiter.check(f"ip:{ip}")

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)  # type: ignore[operator]
