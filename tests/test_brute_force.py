"""Tests for brute_force.py — per-IP failed-auth counter with lockout.

Expected behavior:
- BruteForceGuard tracks failed auth attempts per IP address
- After LOCKOUT_THRESHOLD (default 5) failures, the IP is locked out
- Locked-out IP gets 429 on further attempts (even the 11th+)
- A successful auth resets the failure counter for that IP
- Different IPs have independent counters
- Lockout can expire after a TTL
- is_locked() returns True when locked, False when not
- record_failure() increments counter
- record_success() resets counter
- get_failure_count() returns current count
"""

import time

import pytest

# ---------------------------------------------------------------------------
# Module structure tests — fail with ImportError if module doesn't exist
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """brute_force.py exposes the expected public API."""

    def test_module_importable(self):
        from ponddb import brute_force  # noqa: F401

    def test_brute_force_guard_class_exists(self):
        from ponddb.brute_force import BruteForceGuard

        assert BruteForceGuard is not None

    def test_brute_force_guard_instantiable_with_defaults(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert guard is not None

    def test_brute_force_guard_has_is_locked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert callable(getattr(guard, "is_locked", None))

    def test_brute_force_guard_has_record_failure(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert callable(getattr(guard, "record_failure", None))

    def test_brute_force_guard_has_record_success(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert callable(getattr(guard, "record_success", None))

    def test_brute_force_guard_has_get_failure_count(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert callable(getattr(guard, "get_failure_count", None))

    def test_default_lockout_threshold_is_5(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        threshold = (
            getattr(guard, "lockout_threshold", None)
            or getattr(guard, "_lockout_threshold", None)
            or getattr(guard, "threshold", None)
        )
        assert threshold == 5


# ---------------------------------------------------------------------------
# Happy path: fresh IP, no lockout
# ---------------------------------------------------------------------------


class TestFreshIpNotLocked:
    """A new IP with no failures is not locked."""

    def test_fresh_ip_not_locked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert guard.is_locked("192.168.1.1") is False

    def test_unknown_ip_failure_count_is_zero(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert guard.get_failure_count("10.0.0.1") == 0

    def test_one_failure_not_locked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        guard.record_failure("1.2.3.4")
        assert guard.is_locked("1.2.3.4") is False

    def test_four_failures_not_locked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(4):
            guard.record_failure("5.6.7.8")
        assert guard.is_locked("5.6.7.8") is False

    def test_failure_count_increments(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for i in range(3):
            guard.record_failure("9.9.9.9")
            assert guard.get_failure_count("9.9.9.9") == i + 1


# ---------------------------------------------------------------------------
# Core lockout behavior: 5 failures → locked
# ---------------------------------------------------------------------------


class TestLockoutAfterFiveFailures:
    """5 consecutive failures trigger a 429 lockout for the IP."""

    def test_five_failures_triggers_lockout(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("10.10.10.10")
        assert guard.is_locked("10.10.10.10") is True

    def test_fifth_failure_is_the_lockout_boundary(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(4):
            guard.record_failure("11.11.11.11")
        assert guard.is_locked("11.11.11.11") is False
        guard.record_failure("11.11.11.11")
        assert guard.is_locked("11.11.11.11") is True

    def test_failure_count_at_lockout_is_five(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("20.20.20.20")
        assert guard.get_failure_count("20.20.20.20") == 5

    def test_is_locked_returns_true_type(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("30.30.30.30")
        result = guard.is_locked("30.30.30.30")
        assert result is True


# ---------------------------------------------------------------------------
# Lockout persistence: 11th attempt still locked
# ---------------------------------------------------------------------------


class TestLockoutPersistence:
    """Locked IP stays locked on subsequent attempts (11th, 20th, etc.)."""

    def test_sixth_attempt_still_locked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("50.50.50.50")
        guard.record_failure("50.50.50.50")  # 6th
        assert guard.is_locked("50.50.50.50") is True

    def test_eleventh_attempt_still_locked(self):
        """Explicitly tests the requirement: 11th attempt → still locked."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(11):
            guard.record_failure("60.60.60.60")
        assert guard.is_locked("60.60.60.60") is True

    def test_locked_ip_stays_locked_for_many_attempts(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("70.70.70.70")
        for _ in range(15):
            guard.record_failure("70.70.70.70")
            assert guard.is_locked("70.70.70.70") is True

    def test_failure_count_above_threshold(self):
        """Counter keeps incrementing beyond the lockout threshold."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(11):
            guard.record_failure("80.80.80.80")
        count = guard.get_failure_count("80.80.80.80")
        assert count >= 5  # may be 5 (capped) or 11 (uncapped), both valid


# ---------------------------------------------------------------------------
# Reset on success
# ---------------------------------------------------------------------------


class TestResetOnSuccess:
    """A successful auth resets the failure counter and unlocks the IP."""

    def test_success_resets_counter_to_zero(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(3):
            guard.record_failure("100.100.100.100")
        guard.record_success("100.100.100.100")
        assert guard.get_failure_count("100.100.100.100") == 0

    def test_success_unlocks_locked_ip(self):
        """After being locked, a success must lift the lockout."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("101.101.101.101")
        assert guard.is_locked("101.101.101.101") is True
        guard.record_success("101.101.101.101")
        assert guard.is_locked("101.101.101.101") is False

    def test_success_on_fresh_ip_is_noop(self):
        """record_success on an IP with no failures must not raise or break state."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        guard.record_success("102.102.102.102")  # should not raise
        assert guard.get_failure_count("102.102.102.102") == 0
        assert guard.is_locked("102.102.102.102") is False

    def test_failure_after_success_starts_fresh(self):
        """After a reset, a new sequence of failures starts from zero."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("103.103.103.103")
        guard.record_success("103.103.103.103")
        # Now 4 more failures should not lock (only 4, need 5)
        for _ in range(4):
            guard.record_failure("103.103.103.103")
        assert guard.is_locked("103.103.103.103") is False

    def test_five_failures_after_reset_locks_again(self):
        """After reset, 5 fresh failures lock the IP again."""
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("104.104.104.104")
        guard.record_success("104.104.104.104")
        for _ in range(5):
            guard.record_failure("104.104.104.104")
        assert guard.is_locked("104.104.104.104") is True


# ---------------------------------------------------------------------------
# IP isolation: different IPs have independent counters
# ---------------------------------------------------------------------------


class TestIpIsolation:
    """Each IP address has its own independent failure counter."""

    def test_one_ip_locked_does_not_affect_another(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("200.200.200.200")
        assert guard.is_locked("200.200.200.200") is True
        assert guard.is_locked("201.201.201.201") is False

    def test_different_ips_have_independent_failure_counts(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(3):
            guard.record_failure("210.210.210.210")
        for _ in range(2):
            guard.record_failure("211.211.211.211")
        assert guard.get_failure_count("210.210.210.210") == 3
        assert guard.get_failure_count("211.211.211.211") == 2

    def test_success_on_one_ip_does_not_reset_another(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("220.220.220.220")
        for _ in range(5):
            guard.record_failure("221.221.221.221")
        guard.record_success("220.220.220.220")
        # 221 is still locked
        assert guard.is_locked("221.221.221.221") is True

    def test_many_ips_tracked_independently(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        ips = [f"10.0.0.{i}" for i in range(10)]
        for ip in ips:
            guard.record_failure(ip)
        for ip in ips:
            assert guard.get_failure_count(ip) == 1
            assert guard.is_locked(ip) is False


# ---------------------------------------------------------------------------
# Custom threshold configuration
# ---------------------------------------------------------------------------


class TestCustomThreshold:
    """BruteForceGuard can be configured with a custom lockout threshold."""

    def test_custom_threshold_of_3(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=3)
        for _ in range(2):
            guard.record_failure("1.1.1.1")
        assert guard.is_locked("1.1.1.1") is False
        guard.record_failure("1.1.1.1")
        assert guard.is_locked("1.1.1.1") is True

    def test_custom_threshold_of_10(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=10)
        for _ in range(9):
            guard.record_failure("2.2.2.2")
        assert guard.is_locked("2.2.2.2") is False
        guard.record_failure("2.2.2.2")
        assert guard.is_locked("2.2.2.2") is True

    def test_threshold_of_1_locks_on_first_failure(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_threshold=1)
        guard.record_failure("3.3.3.3")
        assert guard.is_locked("3.3.3.3") is True


# ---------------------------------------------------------------------------
# Lockout TTL / expiry
# ---------------------------------------------------------------------------


class TestLockoutExpiry:
    """Locked IPs are automatically unlocked after the lockout TTL expires."""

    def test_lockout_expires_after_ttl(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_ttl_seconds=1)
        for _ in range(5):
            guard.record_failure("77.77.77.77")
        assert guard.is_locked("77.77.77.77") is True
        time.sleep(1.1)
        assert guard.is_locked("77.77.77.77") is False

    def test_not_locked_before_ttl_expires(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_ttl_seconds=60)
        for _ in range(5):
            guard.record_failure("88.88.88.88")
        # Should still be locked well before 60s
        assert guard.is_locked("88.88.88.88") is True

    def test_failure_count_resets_after_ttl(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard(lockout_ttl_seconds=1)
        for _ in range(5):
            guard.record_failure("99.99.99.99")
        time.sleep(1.1)
        # After TTL, fresh failures should start from scratch
        guard.record_failure("99.99.99.99")
        assert guard.get_failure_count("99.99.99.99") == 1
        assert guard.is_locked("99.99.99.99") is False


# ---------------------------------------------------------------------------
# HTTP integration: check_or_raise raises HTTPException(429) when locked
# ---------------------------------------------------------------------------


class TestHttpIntegration:
    """BruteForceGuard raises HTTPException(429) for locked IPs."""

    def test_check_or_raise_exists(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        assert callable(getattr(guard, "check_or_raise", None))

    def test_check_or_raise_does_not_raise_when_unlocked(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        guard.check_or_raise("1.2.3.4")  # must not raise

    def test_check_or_raise_raises_429_when_locked(self):
        from fastapi import HTTPException

        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("55.55.55.55")
        with pytest.raises(HTTPException) as exc_info:
            guard.check_or_raise("55.55.55.55")
        assert exc_info.value.status_code == 429

    def test_check_or_raise_429_has_detail(self):
        from fastapi import HTTPException

        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("66.66.66.66")
        with pytest.raises(HTTPException) as exc_info:
            guard.check_or_raise("66.66.66.66")
        assert exc_info.value.detail is not None
        assert len(str(exc_info.value.detail)) > 0

    def test_check_or_raise_does_not_raise_after_reset(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("77.77.77.77")
        guard.record_success("77.77.77.77")
        guard.check_or_raise("77.77.77.77")  # must not raise


# ---------------------------------------------------------------------------
# FastAPI middleware integration
# ---------------------------------------------------------------------------


class TestBruteForceMiddleware:
    """BruteForceMiddleware integrates with FastAPI to block locked IPs."""

    def _make_app(self, guard):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from ponddb.brute_force import BruteForceMiddleware

        app = FastAPI()
        app.add_middleware(BruteForceMiddleware, guard=guard)

        @app.post("/login")
        def login(success: bool = False):
            ip = "127.0.0.1"
            if success:
                guard.record_success(ip)
                return {"ok": True}
            else:
                guard.record_failure(ip)
                return {"ok": False}

        return TestClient(app, raise_server_exceptions=False)

    def test_middleware_class_exists(self):
        from ponddb.brute_force import BruteForceMiddleware

        assert BruteForceMiddleware is not None

    def test_first_request_passes(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        client = self._make_app(guard)
        resp = client.get("/login")
        assert resp.status_code != 500

    def test_locked_ip_gets_429_from_middleware(self):
        from ponddb.brute_force import BruteForceGuard

        guard = BruteForceGuard()
        for _ in range(5):
            guard.record_failure("127.0.0.1")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from ponddb.brute_force import BruteForceMiddleware

        app = FastAPI()
        app.add_middleware(BruteForceMiddleware, guard=guard)

        @app.post("/login")
        def login():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/login", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 429

    def test_unlocked_ip_gets_200_from_middleware(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from ponddb.brute_force import BruteForceGuard, BruteForceMiddleware

        guard = BruteForceGuard()
        app = FastAPI()
        app.add_middleware(BruteForceMiddleware, guard=guard)

        @app.post("/login")
        def login():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/login", headers={"X-Forwarded-For": "9.8.7.6"})
        assert resp.status_code == 200
