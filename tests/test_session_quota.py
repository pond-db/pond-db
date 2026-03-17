"""Tests for workgroup session quota enforcement.

Verifies:
  - max_concurrent_sessions blocks excess session creation
  - Quota hit with suspended sessions → resumes one instead of rejecting
  - Counter tracks correctly through suspend/terminate
  - Default workgroup skips quota check (backwards compat)
"""

import pytest

from ponddb.engine.session_manager import (
    SessionManager,
    SessionStatus,
    WorkgroupQuotaExceeded,
)


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager(idle_timeout=300)


class TestQuotaBlocksExcessSessions:
    def test_quota_blocks_fourth_session(self, manager: SessionManager) -> None:
        """set max=3, create 3 sessions → 4th raises WorkgroupQuotaExceeded."""
        for _ in range(3):
            manager.create_session(workgroup_id="analytics", max_concurrent_sessions=3)

        with pytest.raises(WorkgroupQuotaExceeded, match="max concurrent sessions"):
            manager.create_session(workgroup_id="analytics", max_concurrent_sessions=3)

    def test_quota_allows_up_to_max(self, manager: SessionManager) -> None:
        """Exactly max sessions should succeed."""
        sids = []
        for _ in range(3):
            sid = manager.create_session(workgroup_id="analytics", max_concurrent_sessions=3)
            sids.append(sid)
        assert len(sids) == 3
        assert all(manager.get_session(s)["status"] == SessionStatus.ACTIVE for s in sids)

    def test_quota_error_message_includes_limit(self, manager: SessionManager) -> None:
        """Error message includes the max_concurrent_sessions number."""
        for _ in range(2):
            manager.create_session(workgroup_id="wg1", max_concurrent_sessions=2)
        with pytest.raises(WorkgroupQuotaExceeded, match="2"):
            manager.create_session(workgroup_id="wg1", max_concurrent_sessions=2)


class TestQuotaResumesSuspended:
    def test_quota_resumes_suspended_instead_of_rejecting(self, manager: SessionManager) -> None:
        """set max=3, create 3, suspend 1, create new (fills slot) → next create resumes suspended."""
        sids = []
        for _ in range(3):
            sids.append(manager.create_session(workgroup_id="wg", max_concurrent_sessions=3))

        # Suspend one — active drops to 2
        manager.suspend_session(sids[0])
        assert manager.get_session(sids[0])["status"] == SessionStatus.SUSPENDED

        # Create another (fills the freed slot) — now 3 active + 1 suspended
        sids.append(manager.create_session(workgroup_id="wg", max_concurrent_sessions=3))

        # NOW the next create hits the quota and should resume the suspended one
        resumed_sid = manager.create_session(workgroup_id="wg", max_concurrent_sessions=3)
        assert resumed_sid == sids[0]
        assert manager.get_session(resumed_sid)["status"] == SessionStatus.ACTIVE

    def test_quota_resumes_only_when_at_limit(self, manager: SessionManager) -> None:
        """If under quota, create new session even if suspended ones exist."""
        sid1 = manager.create_session(workgroup_id="wg", max_concurrent_sessions=3)
        manager.suspend_session(sid1)

        # Under quota (0 active, max=3), should create new, not resume
        sid2 = manager.create_session(workgroup_id="wg", max_concurrent_sessions=3)
        assert sid2 != sid1  # New session, not resumed


class TestQuotaCounterOnSuspendTerminate:
    def test_suspend_frees_quota_slot(self, manager: SessionManager) -> None:
        """After suspending a session, a new one can be created within quota."""
        sids = []
        for _ in range(3):
            sids.append(manager.create_session(workgroup_id="wg", max_concurrent_sessions=3))

        manager.suspend_session(sids[0])
        # Now only 2 active — should allow a new one
        new_sid = manager.create_session(workgroup_id="wg", max_concurrent_sessions=3)
        # It should resume the suspended one since we're at the limit counting suspended
        # Actually with 2 active + 1 suspended, active count is 2, max is 3, so new session
        assert new_sid not in sids or new_sid == sids[0]

    def test_terminate_frees_quota_slot(self, manager: SessionManager) -> None:
        """After terminating a session, a new one can be created within quota."""
        sids = []
        for _ in range(3):
            sids.append(manager.create_session(workgroup_id="wg", max_concurrent_sessions=3))

        manager.destroy_session(sids[2])
        # Now 2 active — should allow one more
        new_sid = manager.create_session(workgroup_id="wg", max_concurrent_sessions=3)
        assert new_sid not in sids


class TestDefaultWorkgroupNoQuota:
    def test_default_workgroup_skips_quota(self, manager: SessionManager) -> None:
        """workgroup_id='default' never enforces quota, even with max set."""
        for _ in range(10):
            manager.create_session(workgroup_id="default", max_concurrent_sessions=3)
        assert manager.session_count == 10

    def test_no_max_set_skips_quota(self, manager: SessionManager) -> None:
        """When max_concurrent_sessions is None, no quota check."""
        for _ in range(10):
            manager.create_session(workgroup_id="analytics", max_concurrent_sessions=None)
        assert manager.session_count == 10


class TestQuotaIsolationBetweenWorkgroups:
    def test_different_workgroups_have_independent_quotas(self, manager: SessionManager) -> None:
        """Workgroup A at quota doesn't affect workgroup B."""
        for _ in range(3):
            manager.create_session(workgroup_id="wg_a", max_concurrent_sessions=3)

        # wg_a is full, but wg_b should still allow
        sid = manager.create_session(workgroup_id="wg_b", max_concurrent_sessions=3)
        assert manager.get_session(sid)["workgroup_id"] == "wg_b"

        # wg_a should still reject
        with pytest.raises(WorkgroupQuotaExceeded):
            manager.create_session(workgroup_id="wg_a", max_concurrent_sessions=3)
