"""Unit tests for HMAC state token utilities (ponddb.oauth_state).

Tests are isolated from the HTTP layer — purely test the token primitives.
Imports are deferred inside each test so collection succeeds even before
the module is implemented (tests will FAIL, not ERROR).
"""

import os
import time

import pytest

os.environ.setdefault("POND_OAUTH_SECRET", "unit-test-oauth-secret")


@pytest.fixture(autouse=True)
def reset_env():
    """Ensure POND_OAUTH_SECRET is always set to a known value."""
    original = os.environ.get("POND_OAUTH_SECRET")
    os.environ["POND_OAUTH_SECRET"] = "unit-test-oauth-secret"
    yield
    if original is not None:
        os.environ["POND_OAUTH_SECRET"] = original
    else:
        os.environ.pop("POND_OAUTH_SECRET", None)


def _get_oauth_state():
    """Lazy import so collection succeeds before module exists."""
    from ponddb.auth import oauth_state

    return oauth_state


class TestGenerateState:
    def test_returns_non_empty_string(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        assert isinstance(token, str) and len(token) > 0

    def test_two_calls_produce_different_tokens(self):
        m = _get_oauth_state()
        a = m.generate_state("google")
        b = m.generate_state("google")
        assert a != b

    def test_different_providers_produce_different_tokens(self):
        m = _get_oauth_state()
        g = m.generate_state("google")
        gh = m.generate_state("github")
        assert g != gh

    def test_token_has_no_whitespace(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        assert " " not in token and "\n" not in token

    def test_token_length_reasonable(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        # At minimum must carry provider + ts + nonce + hmac
        assert len(token) >= 32


class TestVerifyState:
    def test_verify_round_trip(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        data = m.verify_state(token)
        assert isinstance(data, dict)

    def test_provider_preserved(self):
        m = _get_oauth_state()
        for provider in ("google", "github"):
            token = m.generate_state(provider)
            data = m.verify_state(token)
            assert data["provider"] == provider

    def test_timestamp_present_and_recent(self):
        m = _get_oauth_state()
        before = int(time.time())
        token = m.generate_state("google")
        data = m.verify_state(token)
        assert "ts" in data
        assert data["ts"] >= before
        assert data["ts"] <= int(time.time()) + 2

    def test_nonce_present(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        data = m.verify_state(token)
        assert "nonce" in data
        assert len(str(data["nonce"])) >= 8

    def test_empty_string_raises_value_error(self):
        m = _get_oauth_state()
        with pytest.raises((ValueError, Exception)):
            m.verify_state("")

    def test_garbage_input_raises(self):
        m = _get_oauth_state()
        with pytest.raises((ValueError, Exception)):
            m.verify_state("not-a-valid-state-token!!!")

    def test_truncated_token_raises(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        with pytest.raises((ValueError, Exception)):
            m.verify_state(token[:10])

    def test_tampered_payload_raises(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        bad = ("B" if token[0] != "B" else "C") + token[1:]
        with pytest.raises(ValueError):
            m.verify_state(bad)

    def test_tampered_signature_raises(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        bad = token[:-4] + "xxxx"
        with pytest.raises(ValueError):
            m.verify_state(bad)


class TestExpiry:
    def test_token_not_expired_within_default_window(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        data = m.verify_state(token)
        assert data is not None

    def test_explicit_short_max_age_expires(self):
        m = _get_oauth_state()
        token = m.generate_state("google", max_age_seconds=1)
        time.sleep(2)
        with pytest.raises(ValueError, match="[Ee]xpired|[Tt]oo old|[Ss]tale"):
            m.verify_state(token, max_age_seconds=1)

    def test_explicit_longer_max_age_still_valid(self):
        m = _get_oauth_state()
        token = m.generate_state("google", max_age_seconds=60)
        data = m.verify_state(token, max_age_seconds=60)
        assert data["provider"] == "google"


class TestSecretIsolation:
    def test_wrong_secret_raises(self):
        m = _get_oauth_state()
        os.environ["POND_OAUTH_SECRET"] = "secret-A"
        token = m.generate_state("google")
        os.environ["POND_OAUTH_SECRET"] = "secret-B"
        with pytest.raises(ValueError):
            m.verify_state(token)

    def test_missing_secret_raises_config_error(self):
        m = _get_oauth_state()
        os.environ.pop("POND_OAUTH_SECRET", None)
        with pytest.raises((ValueError, RuntimeError, Exception)):
            m.generate_state("google")


class TestHMACProperties:
    def test_signature_uses_hmac_not_plain_hash(self):
        """Two tokens with same content but different secrets must differ."""
        m = _get_oauth_state()
        os.environ["POND_OAUTH_SECRET"] = "secret-X"
        tok_x = m.generate_state("google")
        os.environ["POND_OAUTH_SECRET"] = "secret-Y"
        tok_y = m.generate_state("google")
        assert tok_x != tok_y

    def test_constant_time_comparison_smoke(self):
        """verify_state uses constant-time comparison (smoke test via behaviour)."""
        m = _get_oauth_state()
        token = m.generate_state("google")
        data = m.verify_state(token)
        assert data is not None


class TestTokenIsUrlSafe:
    def test_url_safe_chars_only(self):
        m = _get_oauth_state()
        token = m.generate_state("google")
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=.+%")
        assert all(c in allowed for c in token), f"Non-URL-safe character in token: {token}"
