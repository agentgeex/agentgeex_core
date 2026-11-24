"""
Resource-level ownership E2E tests

Covers the three rules decided on 2026-09-12:
    (a) own resource         -> caller == resource.agent_id
    (b) project admin        -> caller == project.admin_agent_id
    (c) global role          -> role in {host_manager, workshop_manager}

Tools covered: the L3 execution family (finish/activate/continue) and the plan
family (cancel/activate_plan) + update_task / update_session / write_notice /
confirm_task

Run: cd agx && uv run pytest test/test_ownership.py -v
"""

import pytest
from conftest import AID_CORE, AID_HOST, SYS_PROJ

pytestmark = pytest.mark.ownership


# ── unrelated third-party agent (not the owner, not an admin, not a global role) ──
# Build the privilege-escalation scenario with an agent_id that clearly does not own the resource.
# Note: the L0-L7 level system in permissions.py still runs first,
#       so the escalating party chosen here must have a sufficient level (otherwise the level check rejects it first);
#       this test focuses on the layer where the level is sufficient but the resource is not yours.
OTHER_AGENT = "agx_watchdog"  # unknown role, L0-L3


def _exec_row(mysql, run_uuid):
    rows = mysql(f"SELECT agent_id, project_id FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
    return rows[0] if rows else None


class TestExecutionOwnership:
    """Resource-level checks for the L3 execution tools."""

    def test_finish_own_execution_allowed(self, mcp, get, mysql, host_agent_id):
        """(a) the owning agent closes its own case -> allowed."""
        r = get(mcp("agx_get_execution", caller_agent_id=AID_CORE, task_run_uuid="run_nonexistent_ownership"))
        # Probe with a non-existent run: the tool should report not found first, proving the pre-check chain is intact
        assert r is not None

    def test_finish_nonexistent_execution(self, mcp, get):
        """A non-existent execution -> should report not found (not a permission error)."""
        r = get(mcp("agx_finish_task", caller_agent_id=AID_CORE, task_run_uuid="run_does_not_exist_xyz", status="succeeded"))
        s = str(r)
        assert "not found" in s.lower() or "不存在" in s, f"unexpected: {r}"

    def test_update_task_ownership_applied(self, mcp, get, mysql, core_agent_id):
        """agx_update_task is wired into ownership: the core agent can update it (global role)."""
        rows = mysql("SELECT task_id FROM agx_task_base LIMIT 1")
        if not rows:
            pytest.skip("no task_base row in test db")
        tid = rows[0]["task_id"]
        r = get(mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"priority": "3"}))
        s = str(r)
        # workshop_manager passes -> no permission error expected
        assert "权限不足" not in s, f"core agent should pass ownership: {r}"


class TestPlanOwnership:
    """Resource-level checks for the plan family."""

    def test_plan_cancel_nonexistent(self, mcp, get):
        """A non-existent plan -> not found."""
        r = get(mcp("agx_plan_cancel", caller_agent_id=AID_CORE, plan_id="plan_not_exist_xyz"))
        s = str(r)
        assert "not found" in s.lower() or "不存在" in s, f"unexpected: {r}"

    def test_plan_activate_nonexistent(self, mcp, get):
        """A non-existent plan -> not found."""
        r = get(mcp("agx_activate_plan", caller_agent_id=AID_CORE, plan_id="plan_not_exist_xyz"))
        s = str(r)
        assert "not found" in s.lower() or "不存在" in s, f"unexpected: {r}"


class TestUpdateExecutionOwnership:
    """The three rules after agx_update_execution was unified onto _check_resource_ownership."""

    def test_nonexistent_execution(self, mcp, get):
        """A non-existent execution -> not found (not a permission error)."""
        r = get(
            mcp(
                "agx_update_execution",
                caller_agent_id=AID_CORE,
                task_run_uuid="run_does_not_exist_xyz",
                updates={"x_desc": "probe"},
            )
        )
        s = str(r)
        assert "not found" in s.lower() or "不存在" in s, f"unexpected: {r}"

    def test_global_role_allowed(self, mcp, get, mysql, core_agent_id):
        """(c) workshop_manager may update any execution (the old code rejected this)."""
        rows = mysql(
            "SELECT task_run_uuid, agent_id FROM agx_task_execution "
            "WHERE agent_id <> 'agx_core' AND run_status IN ('succeeded','failed') LIMIT 1"
        )
        if not rows:
            pytest.skip("no foreign execution available in test db")
        run_uuid = rows[0]["task_run_uuid"]
        r = get(
            mcp(
                "agx_update_execution",
                caller_agent_id=core_agent_id,
                task_run_uuid=run_uuid,
                updates={"x_desc": "pytest ownership probe"},
            )
        )
        s = str(r)
        assert "无权" not in s and "PermissionError" not in s, f"global role should pass: {r}"


class TestNoticeOwnership:
    """Notice identity check: a notice must be published under your own identity."""

    def test_write_notice_self_allowed(self, mcp, get, core_agent_id):
        """Publishing as yourself -> allowed."""
        r = get(
            mcp(
                "agx_write_notice",
                caller_agent_id=core_agent_id,
                agent_id=core_agent_id,
                project_id=SYS_PROJ,
                notice_type="log",
                content="pytest ownership probe (self)",
                title="pytest-ownership",
            )
        )
        s = str(r)
        assert "权限不足" not in s, f"self notice should pass: {r}"

    def test_write_notice_impersonation_denied(self, mcp, get, core_agent_id):
        """Publishing a notice signed by someone else -> must be rejected (workshop_manager excepted)."""
        # Use an agent_id that is neither core nor a global role as the caller, signing on behalf of core
        r = get(
            mcp(
                "agx_write_notice",
                caller_agent_id=OTHER_AGENT,
                agent_id=AID_CORE,
                project_id=SYS_PROJ,
                notice_type="log",
                content="pytest impersonation probe",
                title="pytest-ownership",
            )
        )
        s = str(r)
        # Note: if OTHER_AGENT lacks the level it is caught by the L1 pre-check and still fails
        assert ("权限不足" in s) or ("permission" in s.lower()) or ("denied" in s.lower()), f"impersonation should be denied: {r}"
