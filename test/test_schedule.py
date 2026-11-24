"""
Scheduling and activation E2E tests
Covers: agx_schedule_task, agx_activate_task, scheduled_at
"""

import re

import pytest

pytestmark = pytest.mark.schedule


def _extract_task_id(text: str) -> str:
    m = re.search(r"(task_[a-f0-9]+)", text)
    return m.group(1) if m else ""


class TestScheduleTask:
    def test_schedule_pending(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_schedule_task", caller_agent_id="agx_core", instruction="E2E - pending task", agent_id=core_agent_id
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"schedule failed: {r}"
        ru = r["run_uuid"]

        row = mysql(f"SELECT run_status FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row and row[0]["run_status"] == "pending"

        # activate
        ar = mcp("agx_activate_task", caller_agent_id="agx_core", task_run_uuid=ru)
        assert isinstance(ar, dict) and "run_uuid" in ar

        row2 = mysql(f"SELECT run_status FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row2 and row2[0]["run_status"] == "running"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_schedule_with_parent(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_schedule_task", caller_agent_id="agx_core", instruction="E2E - parent task", agent_id=core_agent_id
        )
        ru_parent = r["run_uuid"]

        r2 = mcp(
            "agx_schedule_task",
            caller_agent_id="agx_core",
            instruction="E2E - child task",
            agent_id=core_agent_id,
            parent_run_uuid=ru_parent,
        )
        ru_child = r2["run_uuid"]

        row = mysql(f"SELECT parent_run_uuid FROM agx_task_execution WHERE task_run_uuid='{ru_child}'")
        assert row and row[0]["parent_run_uuid"] == ru_parent

        # cleanup
        for ru in [ru_child, ru_parent]:
            mcp(
                "agx_finish_task",
                caller_agent_id="agx_core",
                task_run_uuid=ru,
                status="succeeded",
                summary="pytest cleanup",
            )

    def test_schedule_with_prev_next(self, mcp, mysql, core_agent_id):
        r1 = mcp("agx_schedule_task", caller_agent_id="agx_core", instruction="E2E - first", agent_id=core_agent_id)
        ru1 = r1["run_uuid"]

        r2 = mcp(
            "agx_schedule_task",
            caller_agent_id="agx_core",
            instruction="E2E - second",
            agent_id=core_agent_id,
            prev_task=ru1,
        )
        ru2 = r2["run_uuid"]

        row = mysql(f"SELECT prev_task FROM agx_task_execution WHERE task_run_uuid='{ru2}'")
        assert row and row[0]["prev_task"] == ru1

        for ru in [ru2, ru1]:
            mcp(
                "agx_finish_task",
                caller_agent_id="agx_core",
                task_run_uuid=ru,
                status="succeeded",
                summary="pytest cleanup",
            )

    def test_schedule_no_parent_null_defaults(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_schedule_task", caller_agent_id="agx_core", instruction="E2E - no parent", agent_id=core_agent_id
        )
        ru = r["run_uuid"]

        row = mysql(
            f"SELECT parent_task_id, parent_run_uuid, prev_task, next_task FROM agx_task_execution WHERE task_run_uuid='{ru}'"
        )
        assert row
        for col in ["parent_task_id", "parent_run_uuid", "prev_task", "next_task"]:
            assert row[0][col] in (
                None,
                "",
            ), f"{col} should be empty, got: {row[0][col]}"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_with_scheduled_at(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_schedule_task",
            caller_agent_id="agx_core",
            instruction="E2E - scheduled future",
            agent_id=core_agent_id,
            scheduled_at="2099-01-01 00:00:00",
        )
        ru = r["run_uuid"]

        row = mysql(f"SELECT scheduled_at FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row and row[0]["scheduled_at"] is not None, "scheduled_at should not be NULL"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_reply_mode_silent(self, mcp, mysql):
        """silent task -> needs_reply=0 when scheduled"""
        rows = mysql(
            "SELECT task_id FROM agx_task_base "
            "WHERE task_labs='system_check' AND agent_id='agx_core' "
            "AND active_status=1 LIMIT 1"
        )
        tid = rows[0]["task_id"]

        r = mcp(
            "agx_schedule_task",
            caller_agent_id="agx_core",
            instruction="E2E - silent schedule",
            task_id=tid,
            agent_id="agx_core",
        )
        ru = r["run_uuid"]

        row = mysql(f"SELECT needs_reply FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row and str(row[0]["needs_reply"]) == "0"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )


class TestActivateTask:
    def test_activate_not_pending_rejected(self, mcp, mysql, core_agent_id):
        """activating an execution that is already running should be rejected"""
        dr = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - already running",
            agent_id=core_agent_id,
        )
        ru = dr["run_uuid"]

        ar = mcp("agx_activate_task", caller_agent_id="agx_core", task_run_uuid=ru)
        msg = str(ar)
        assert "error" in msg.lower() or "not 'pending'" in msg.lower(), f"should reject non-pending activation: {ar}"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )
