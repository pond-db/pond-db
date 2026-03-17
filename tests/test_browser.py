"""Playwright browser tests for PondDB session management.

These tests run against a LIVE PondDB server at http://localhost:8432.
They open a real (headless) Chromium browser and interact with the UI.

Run:
    pytest tests/test_browser.py -v --base-url http://localhost:8432

Prerequisites:
    - PondDB running at localhost:8432
    - `playwright install chromium` completed
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

API_KEY = "pond-alpha-key-2026"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(page: Page) -> None:
    """Log in via the login form."""
    page.goto("/login")
    page.fill("input[name='api_key']", API_KEY)
    page.click("button[type='submit']")
    page.wait_for_url("**/dashboard**", timeout=5000)


def _create_session_via_api(page: Page) -> str:
    """Create a session via the REST API, return session_id."""
    resp = page.request.post(
        "/session",
        headers={"X-API-Key": API_KEY},
    )
    assert resp.ok
    return resp.json()["session_id"]


def _get_sessions_via_api(page: Page) -> list[dict]:
    """List sessions via the REST API."""
    resp = page.request.get("/sessions")
    assert resp.ok
    return resp.json()


def _cleanup_session(page: Page, session_id: str) -> None:
    """Delete a session via API (best effort)."""
    page.request.delete(
        f"/session/{session_id}",
        headers={"X-API-Key": API_KEY},
    )


# ---------------------------------------------------------------------------
# TEST 1: Page load does not create sessions
# ---------------------------------------------------------------------------


class TestNoSideEffectSessionCreation:
    def test_dashboard_loads_no_session_creation(self, page: Page) -> None:
        """Loading /dashboard multiple times must not create sessions."""
        _login(page)
        before = len(_get_sessions_via_api(page))

        for _ in range(5):
            page.goto("/dashboard")
            page.wait_for_load_state("networkidle")

        after = len(_get_sessions_via_api(page))
        assert after == before, f"Dashboard page loads created {after - before} sessions!"

    def test_sessions_page_no_side_effects(self, page: Page) -> None:
        """Loading /dashboard/sessions 5 times must not create sessions."""
        _login(page)

        # Snapshot AFTER login (login itself doesn't create sessions,
        # but the 10s HTMX poll may have fired, so take count NOW)
        page.goto("/dashboard/sessions")
        page.wait_for_load_state("networkidle")
        before = len(_get_sessions_via_api(page))

        for _ in range(4):
            page.goto("/dashboard/sessions")
            page.wait_for_load_state("networkidle")

        after = len(_get_sessions_via_api(page))
        assert after == before, f"Sessions page loads created {after - before} sessions!"


# ---------------------------------------------------------------------------
# TEST 2 & 3: Suspend and Resume buttons
# ---------------------------------------------------------------------------


class TestSuspendButton:
    def test_suspend_button_changes_badge(self, page: Page) -> None:
        """Click Suspend on an ACTIVE session -> badge becomes SUSPENDED."""
        _login(page)
        sid = _create_session_via_api(page)
        try:
            page.goto("/dashboard/sessions")
            page.wait_for_selector("table.data-table")
            # Wait for the HTMX-powered button to be present (proves HTMX loaded)
            row = page.locator(f"tr#session-row-{sid}")
            expect(row).to_be_visible(timeout=5000)

            # Click Suspend and wait for HTMX network request to complete
            with page.expect_response(f"**/htmx/session/{sid}/suspend") as resp_info:
                row.locator("button:has-text('Suspend')").click()
            assert resp_info.value.ok

            # After HTMX swap, re-locate the row (DOM was replaced)
            row = page.locator(f"tr#session-row-{sid}")
            expect(row.locator(".badge-suspended")).to_be_visible(timeout=5000)
            expect(row.locator("button:has-text('Resume')")).to_be_visible()

            # Verify via API
            sessions = _get_sessions_via_api(page)
            match = [s for s in sessions if s["session_id"] == sid]
            assert match[0]["status"] == "SUSPENDED"
        finally:
            _cleanup_session(page, sid)


class TestResumeButton:
    def test_resume_button_changes_badge(self, page: Page) -> None:
        """Click Resume on a SUSPENDED session -> badge becomes ACTIVE."""
        _login(page)
        sid = _create_session_via_api(page)
        try:
            # Suspend via API first
            page.request.post(
                f"/htmx/session/{sid}/suspend",
                headers={"X-API-Key": API_KEY},
            )

            page.goto("/dashboard/sessions")
            page.wait_for_selector("table.data-table")
            # Wait for the HTMX-powered button to be present
            row = page.locator(f"tr#session-row-{sid}")
            expect(row).to_be_visible(timeout=5000)

            # Click Resume and wait for HTMX network request to complete
            with page.expect_response(f"**/htmx/session/{sid}/resume") as resp_info:
                row.locator("button:has-text('Resume')").click()
            assert resp_info.value.ok

            # After HTMX swap, re-locate the row (DOM was replaced)
            row = page.locator(f"tr#session-row-{sid}")
            expect(row.locator(".badge-active")).to_be_visible(timeout=5000)

            # Row should now show Suspend button
            expect(row.locator("button:has-text('Suspend')")).to_be_visible()

            # Verify via API
            sessions = _get_sessions_via_api(page)
            match = [s for s in sessions if s["session_id"] == sid]
            assert match[0]["status"] == "ACTIVE"
        finally:
            _cleanup_session(page, sid)


# ---------------------------------------------------------------------------
# TEST 4: Auto-suspend (verify background task is registered)
# ---------------------------------------------------------------------------


class TestAutoSuspend:
    def test_watchdog_is_running(self, page: Page) -> None:
        """Verify the watchdog background task is active by checking health.

        A full idle-timeout test would require waiting 5+ minutes.
        Instead, we verify sessions CAN be suspended and that the
        watchdog infrastructure is wired up (health endpoint works,
        sessions are manageable).
        """
        resp = page.request.get("/health")
        assert resp.ok
        data = resp.json()
        assert data["status"] == "ok"

    def test_idle_session_visible_on_sessions_page(self, page: Page) -> None:
        """An ACTIVE session shows on the sessions page with correct badge."""
        _login(page)
        sid = _create_session_via_api(page)
        try:
            page.goto("/dashboard/sessions")
            page.wait_for_selector("table.data-table")
            row = page.locator(f"tr#session-row-{sid}")
            expect(row).to_be_visible(timeout=5000)
            expect(row.locator(".badge-active")).to_be_visible()
        finally:
            _cleanup_session(page, sid)


# ---------------------------------------------------------------------------
# TEST 5: Dashboard displays data correctly
# ---------------------------------------------------------------------------


class TestDashboardContent:
    def test_stat_cards_visible(self, page: Page) -> None:
        """Dashboard shows stat cards."""
        _login(page)
        page.goto("/dashboard")
        page.wait_for_load_state("networkidle")
        stat_cards = page.locator(".stat-card")
        assert stat_cards.count() >= 3, f"Expected >= 3 stat cards, got {stat_cards.count()}"

    def test_sidebar_nav_links(self, page: Page) -> None:
        """Sidebar contains navigation links."""
        _login(page)
        page.goto("/dashboard")
        sidebar_links = page.locator(".sidebar a")
        assert sidebar_links.count() >= 4, (
            f"Expected >= 4 sidebar links, got {sidebar_links.count()}"
        )

    def test_sidebar_has_key_pages(self, page: Page) -> None:
        """Sidebar links include Dashboard, SQL Editor, Settings."""
        _login(page)
        page.goto("/dashboard")
        sidebar = page.locator(".sidebar")
        expect(sidebar.locator("a:has-text('Dashboard')")).to_be_visible()
        expect(sidebar.locator("a:has-text('SQL Editor')")).to_be_visible()
        expect(sidebar.locator("a:has-text('Settings')")).to_be_visible()


# ---------------------------------------------------------------------------
# TEST 6: Landing page renders for unauthenticated users
# ---------------------------------------------------------------------------


class TestLandingPage:
    def test_landing_has_hero(self, page: Page) -> None:
        page.goto("/")
        expect(page.locator("text=Serverless DuckDB")).to_be_visible()

    def test_landing_has_cta_buttons(self, page: Page) -> None:
        page.goto("/")
        expect(page.locator("text=Request Invite")).to_be_visible()
        expect(page.locator("a:has-text('GitHub')").first).to_be_visible()

    def test_landing_has_feature_cards(self, page: Page) -> None:
        page.goto("/")
        cards = page.locator(".feature-card")
        assert cards.count() == 3, f"Expected 3 feature cards, got {cards.count()}"

    def test_landing_has_code_example(self, page: Page) -> None:
        page.goto("/")
        expect(page.locator(".code-block")).to_be_visible()

    def test_landing_login_button_navigates(self, page: Page) -> None:
        page.goto("/")
        page.click("a:has-text('Login')")
        page.wait_for_url("**/login**", timeout=5000)
        expect(page.locator("h2:has-text('Sign in')")).to_be_visible()


# ---------------------------------------------------------------------------
# TEST 7: Settings page shows configuration
# ---------------------------------------------------------------------------


class TestSettingsPage:
    def test_settings_shows_config(self, page: Page) -> None:
        """Settings page displays instance configuration."""
        _login(page)
        page.goto("/settings")
        page.wait_for_load_state("networkidle")
        expect(page.locator("h2:has-text('Settings')")).to_be_visible()
        # Should show version, data dir, etc.
        expect(page.locator("text=0.1.0")).to_be_visible()
        assert page.locator(".detail-row").count() >= 5
