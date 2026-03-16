"""Tests for rate_limit.py — Redis sliding window rate limiter.

Defines expected behavior for:
- RateLimiter.check(): allows requests within limit, blocks at 101st
- RateLimiter.check(): returns positive Retry-After seconds on block
- RateLimiter.check(): fail-open on Redis error (allow + logs warning)
- RateLimitMiddleware: per-IP and per-API-key limiting via sliding window
- RateLimitMiddleware: 429 status + Retry-After header on limit exceeded
- RateLimitMiddleware: passes through request on Redis failure (fail-open)
- Key isolation: different IPs / API keys have independent counters
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# FakeRedis: in-memory sorted-set operations for testing
# ---------------------------------------------------------------------------


class FakePipeline:
    """Collect commands and execute sequentially against the parent FakeRedis."""

    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis
        self._cmds: list = []

    def zadd(self, key: str, mapping: dict, **kwargs) -> "FakePipeline":
        self._cmds.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key: str, min_val, max_val) -> "FakePipeline":
        self._cmds.append(("zremrangebyscore", key, min_val, max_val))
        return self

    def zcard(self, key: str) -> "FakePipeline":
        self._cmds.append(("zcard", key))
        return self

    def expire(self, key: str, seconds: int) -> "FakePipeline":
        self._cmds.append(("expire", key, seconds))
        return self

    async def execute(self) -> list:
        if self._redis._fail:
            raise ConnectionError("Redis unavailable")
        results = []
        for cmd in self._cmds:
            if cmd[0] == "zadd":
                _, key, mapping = cmd
                results.append(await self._redis.zadd(key, mapping))
            elif cmd[0] == "zremrangebyscore":
                _, key, mn, mx = cmd
                results.append(await self._redis.zremrangebyscore(key, mn, mx))
            elif cmd[0] == "zcard":
                _, key = cmd
                results.append(await self._redis.zcard(key))
            elif cmd[0] == "expire":
                _, key, seconds = cmd
                results.append(await self._redis.expire(key, seconds))
        return results

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *args) -> None:
        pass


class FakeRedis:
    """In-memory Redis replacement supporting sorted-set sliding window operations."""

    def __init__(self, fail: bool = False) -> None:
        # key -> list of (score, member) tuples
        self._sets: dict[str, list[tuple[float, str]]] = {}
        self._fail = fail

    def _check(self) -> None:
        if self._fail:
            raise ConnectionError("Redis unavailable")

    async def zadd(self, key: str, mapping: dict, **kwargs) -> int:
        self._check()
        if key not in self._sets:
            self._sets[key] = []
        existing = {m for _, m in self._sets[key]}
        added = 0
        for member, score in mapping.items():
            if member not in existing:
                self._sets[key].append((score, member))
                added += 1
        return added

    async def zremrangebyscore(self, key: str, min_val, max_val) -> int:
        self._check()
        if key not in self._sets:
            return 0
        mn = float("-inf") if min_val in ("-inf", float("-inf")) else float(min_val)
        mx = float("+inf") if max_val in ("+inf", float("+inf")) else float(max_val)
        before = len(self._sets[key])
        self._sets[key] = [(s, m) for s, m in self._sets[key] if not (mn <= s <= mx)]
        return before - len(self._sets[key])

    async def zcard(self, key: str) -> int:
        self._check()
        return len(self._sets.get(key, []))

    async def expire(self, key: str, seconds: int) -> bool:
        self._check()
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(redis_client, limit: int = 100, window_seconds: int = 60) -> FastAPI:
    from ponddb.rate_limit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        redis_client=redis_client,
        limit=limit,
        window_seconds=window_seconds,
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def _make_limiter(redis, limit: int = 100, window_seconds: int = 60):
    from ponddb.rate_limit import RateLimiter

    return RateLimiter(redis, limit=limit, window_seconds=window_seconds)


# ---------------------------------------------------------------------------
# Unit tests: RateLimiter.check()
# ---------------------------------------------------------------------------


class TestRateLimiterAllow:
    """RateLimiter allows requests under the limit."""

    @pytest.mark.asyncio
    async def test_first_request_allowed(self):
        limiter = _make_limiter(FakeRedis())
        allowed, retry_after = await limiter.check("ip:127.0.0.1")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_retry_after_is_zero_when_allowed(self):
        limiter = _make_limiter(FakeRedis())
        allowed, retry_after = await limiter.check("ip:127.0.0.1")
        assert allowed is True
        assert retry_after == 0

    @pytest.mark.asyncio
    async def test_100th_request_allowed(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=100)
        for _ in range(99):
            await limiter.check("ip:10.0.0.1")
        allowed, _ = await limiter.check("ip:10.0.0.1")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_returns_tuple_of_bool_and_int(self):
        limiter = _make_limiter(FakeRedis())
        result = await limiter.check("ip:1.2.3.4")
        assert isinstance(result, tuple)
        assert len(result) == 2
        allowed, retry_after = result
        assert isinstance(allowed, bool)
        assert isinstance(retry_after, int)


class TestRateLimiterBlock:
    """RateLimiter blocks at the 101st request and returns Retry-After."""

    @pytest.mark.asyncio
    async def test_101st_request_blocked(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=100)
        for _ in range(100):
            await limiter.check("ip:10.0.0.5")
        allowed, _ = await limiter.check("ip:10.0.0.5")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_retry_after_positive_when_blocked(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=100, window_seconds=60)
        for _ in range(100):
            await limiter.check("ip:10.0.0.6")
        _, retry_after = await limiter.check("ip:10.0.0.6")
        assert retry_after > 0

    @pytest.mark.asyncio
    async def test_retry_after_at_most_window_seconds(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=100, window_seconds=60)
        for _ in range(100):
            await limiter.check("ip:10.0.0.7")
        _, retry_after = await limiter.check("ip:10.0.0.7")
        assert retry_after <= 60

    @pytest.mark.asyncio
    async def test_subsequent_requests_also_blocked(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=100)
        for _ in range(100):
            await limiter.check("ip:10.0.0.8")
        for _ in range(5):
            allowed, _ = await limiter.check("ip:10.0.0.8")
            assert allowed is False

    @pytest.mark.asyncio
    async def test_small_limit_blocks_correctly(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=3, window_seconds=60)
        for _ in range(3):
            await limiter.check("ip:192.168.1.1")
        allowed, _ = await limiter.check("ip:192.168.1.1")
        assert allowed is False


class TestRateLimiterKeyIsolation:
    """Different keys have independent counters."""

    @pytest.mark.asyncio
    async def test_different_ips_independent(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=3)
        for _ in range(3):
            await limiter.check("ip:1.1.1.1")
        allowed, _ = await limiter.check("ip:2.2.2.2")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_different_api_keys_independent(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=3)
        for _ in range(3):
            await limiter.check("key:alice-token")
        allowed, _ = await limiter.check("key:bob-token")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_ip_and_api_key_namespaces_independent(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=3)
        for _ in range(3):
            await limiter.check("ip:5.5.5.5")
        allowed, _ = await limiter.check("key:5.5.5.5")  # same value, different namespace
        assert allowed is True


class TestRateLimiterRedisFailure:
    """RateLimiter fails open on Redis errors and logs a warning."""

    @pytest.mark.asyncio
    async def test_redis_failure_allows_request(self):
        limiter = _make_limiter(FakeRedis(fail=True), limit=100)
        allowed, retry_after = await limiter.check("ip:1.2.3.4")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_redis_failure_retry_after_is_zero(self):
        limiter = _make_limiter(FakeRedis(fail=True), limit=100)
        _, retry_after = await limiter.check("ip:1.2.3.4")
        assert retry_after == 0

    @pytest.mark.asyncio
    async def test_redis_failure_logs_warning(self, caplog):
        limiter = _make_limiter(FakeRedis(fail=True), limit=100)
        with caplog.at_level(logging.WARNING):
            await limiter.check("ip:1.2.3.4")
        assert any(
            "redis" in r.message.lower() or "rate" in r.message.lower()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )

    @pytest.mark.asyncio
    async def test_does_not_raise_on_redis_failure(self):
        limiter = _make_limiter(FakeRedis(fail=True), limit=100)
        try:
            await limiter.check("ip:9.9.9.9")
        except Exception as exc:
            pytest.fail(f"RateLimiter.check() raised on Redis failure: {exc}")


# ---------------------------------------------------------------------------
# Middleware integration tests via FastAPI TestClient
# ---------------------------------------------------------------------------


class TestRateLimitMiddlewareBasic:
    """Middleware allows normal traffic and wires into FastAPI cleanly."""

    def test_first_request_200(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        resp = client.get("/ping")
        assert resp.status_code == 200

    def test_response_body_intact(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        resp = client.get("/ping")
        assert resp.json() == {"ok": True}

    def test_99_requests_all_200(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        for i in range(99):
            resp = client.get("/ping", headers={"X-Forwarded-For": "10.0.0.1"})
            assert resp.status_code == 200, f"Request {i+1} expected 200, got {resp.status_code}"


class TestRateLimitMiddleware429:
    """Middleware returns 429 + Retry-After on the 101st request."""

    def test_101st_request_is_429(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        ip = "11.22.33.44"
        for _ in range(100):
            client.get("/ping", headers={"X-Forwarded-For": ip})
        resp = client.get("/ping", headers={"X-Forwarded-For": ip})
        assert resp.status_code == 429

    def test_429_includes_retry_after_header(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        ip = "55.66.77.88"
        for _ in range(100):
            client.get("/ping", headers={"X-Forwarded-For": ip})
        resp = client.get("/ping", headers={"X-Forwarded-For": ip})
        assert resp.status_code == 429
        assert "retry-after" in resp.headers, "429 response must include Retry-After header"

    def test_retry_after_header_is_positive_integer(self):
        client = TestClient(_make_app(FakeRedis(), limit=100, window_seconds=60))
        ip = "99.11.22.33"
        for _ in range(100):
            client.get("/ping", headers={"X-Forwarded-For": ip})
        resp = client.get("/ping", headers={"X-Forwarded-For": ip})
        retry_after = int(resp.headers["retry-after"])
        assert retry_after > 0

    def test_retry_after_header_not_exceeds_window(self):
        client = TestClient(_make_app(FakeRedis(), limit=100, window_seconds=60))
        ip = "44.55.66.77"
        for _ in range(100):
            client.get("/ping", headers={"X-Forwarded-For": ip})
        resp = client.get("/ping", headers={"X-Forwarded-For": ip})
        retry_after = int(resp.headers["retry-after"])
        assert retry_after <= 60

    def test_429_body_has_detail(self):
        client = TestClient(_make_app(FakeRedis(), limit=100))
        ip = "22.33.44.55"
        for _ in range(100):
            client.get("/ping", headers={"X-Forwarded-For": ip})
        resp = client.get("/ping", headers={"X-Forwarded-For": ip})
        assert resp.status_code == 429
        body = resp.json()
        assert "detail" in body or "error" in body or "message" in body


class TestPerIPRateLimit:
    """Rate limiting is enforced per client IP address."""

    def test_different_ips_have_separate_limits(self):
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping", headers={"X-Forwarded-For": "1.1.1.1"})
        resp_a = client.get("/ping", headers={"X-Forwarded-For": "1.1.1.1"})
        assert resp_a.status_code == 429
        resp_b = client.get("/ping", headers={"X-Forwarded-For": "2.2.2.2"})
        assert resp_b.status_code == 200

    def test_uses_x_forwarded_for_header(self):
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping", headers={"X-Forwarded-For": "3.3.3.3"})
        resp = client.get("/ping", headers={"X-Forwarded-For": "3.3.3.3"})
        assert resp.status_code == 429

    def test_falls_back_to_client_host_without_x_forwarded_for(self):
        """Without X-Forwarded-For, middleware must still not crash."""
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping")
        resp = client.get("/ping")
        assert resp.status_code in (200, 429)


class TestPerApiKeyRateLimit:
    """Rate limiting is enforced per API key, separate from IP limits."""

    def test_different_api_keys_have_separate_limits(self):
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping", headers={"X-API-Key": "key-alice"})
        resp_alice = client.get("/ping", headers={"X-API-Key": "key-alice"})
        assert resp_alice.status_code == 429
        resp_bob = client.get("/ping", headers={"X-API-Key": "key-bob"})
        assert resp_bob.status_code == 200

    def test_api_key_present_uses_api_key_for_limiting(self):
        """When X-API-Key is present it must be one of the rate-limit dimensions."""
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping", headers={"X-API-Key": "key-carol", "X-Forwarded-For": "8.8.8.8"})
        resp = client.get("/ping", headers={"X-API-Key": "key-carol", "X-Forwarded-For": "8.8.8.8"})
        assert resp.status_code == 429

    def test_api_key_and_ip_limits_do_not_cross_contaminate(self):
        """Exhausting the key-carol API-key counter must not block key-dave."""
        client = TestClient(_make_app(FakeRedis(), limit=3))
        for _ in range(3):
            client.get("/ping", headers={"X-API-Key": "key-carol"})
        resp = client.get("/ping", headers={"X-API-Key": "key-dave"})
        assert resp.status_code == 200


class TestRateLimitMiddlewareRedisFailure:
    """Middleware fails open when Redis is unavailable."""

    def test_redis_failure_returns_200(self):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        resp = client.get("/ping")
        assert resp.status_code == 200

    def test_redis_failure_response_body_intact(self):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        resp = client.get("/ping")
        assert resp.json() == {"ok": True}

    def test_redis_failure_does_not_return_500(self):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        resp = client.get("/ping")
        assert resp.status_code != 500

    def test_redis_failure_does_not_return_429(self):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        resp = client.get("/ping")
        assert resp.status_code != 429

    def test_multiple_requests_succeed_on_redis_failure(self):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        for _ in range(10):
            resp = client.get("/ping")
            assert resp.status_code == 200

    def test_redis_failure_logs_warning(self, caplog):
        client = TestClient(_make_app(FakeRedis(fail=True), limit=100))
        with caplog.at_level(logging.WARNING):
            client.get("/ping")
        assert any(
            "redis" in r.message.lower()
            or "rate" in r.message.lower()
            or "limit" in r.message.lower()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )


# ---------------------------------------------------------------------------
# Module-level structure tests
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """rate_limit.py exposes the expected public API."""

    def test_rate_limiter_class_exists(self):
        from ponddb.rate_limit import RateLimiter

        assert RateLimiter is not None

    def test_rate_limit_middleware_class_exists(self):
        from ponddb.rate_limit import RateLimitMiddleware

        assert RateLimitMiddleware is not None

    def test_rate_limiter_instantiation(self):
        from ponddb.rate_limit import RateLimiter

        limiter = RateLimiter(FakeRedis(), limit=100, window_seconds=60)
        assert limiter is not None

    def test_rate_limiter_has_check_method(self):
        from ponddb.rate_limit import RateLimiter

        limiter = RateLimiter(FakeRedis())
        assert callable(getattr(limiter, "check", None))

    def test_rate_limit_middleware_is_starlette_middleware(self):
        from starlette.middleware.base import BaseHTTPMiddleware

        from ponddb.rate_limit import RateLimitMiddleware

        assert issubclass(RateLimitMiddleware, BaseHTTPMiddleware)

    def test_rate_limiter_default_limit_is_100(self):
        from ponddb.rate_limit import RateLimiter

        limiter = RateLimiter(FakeRedis())
        limit = getattr(limiter, "_limit", None) or getattr(limiter, "limit", None)
        assert limit == 100

    def test_rate_limiter_default_window_is_60(self):
        from ponddb.rate_limit import RateLimiter

        limiter = RateLimiter(FakeRedis())
        window = getattr(limiter, "_window_seconds", None) or getattr(
            limiter, "window_seconds", None
        )
        assert window == 60


# ---------------------------------------------------------------------------
# Edge case: exact limit boundary
# ---------------------------------------------------------------------------


class TestExactLimitBoundary:
    """Precise boundary testing: exactly at limit vs. one over."""

    @pytest.mark.asyncio
    async def test_exactly_at_limit_is_allowed(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=5)
        results = []
        for _ in range(5):
            allowed, _ = await limiter.check("ip:boundary-test")
            results.append(allowed)
        assert all(results), "All 5 requests at the limit must be allowed"

    @pytest.mark.asyncio
    async def test_one_over_limit_is_blocked(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=5)
        for _ in range(5):
            await limiter.check("ip:over-limit-test")
        allowed, _ = await limiter.check("ip:over-limit-test")
        assert allowed is False, "6th request (one over limit=5) must be blocked"

    @pytest.mark.asyncio
    async def test_limit_of_one_blocks_second(self):
        redis = FakeRedis()
        limiter = _make_limiter(redis, limit=1)
        allowed1, _ = await limiter.check("ip:limit-one")
        allowed2, _ = await limiter.check("ip:limit-one")
        assert allowed1 is True
        assert allowed2 is False
