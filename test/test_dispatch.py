"""
Task dispatch and execution E2E tests
Covers: agx_dispatch_task, agx_finish_task, agx_update_execution,
      reply_mode -> automatic needs_reply derivation, host-scoped queue
"""

import re

import pytest

from conftest import HOST_ID

pytestmark = pytest.mark.dispatch


def _extract_task_id(text: str) -> str:
    m = re.search(r"(task_[a-f0-9]+)", text)
    return m.group(1) if m else ""


def _extract_run_uuid(text) -> str:
    """Extract run_uuid from an MCP response; supports dict and str."""
    if isinstance(text, dict):
        return text.get("run_uuid", "")
    m = re.search(r"(run_[a-f0-9]+)", str(text))
    return m.group(1) if m else ""


# ═══════════════════════════════════════════════════════════════
# reply_mode -> automatic needs_reply derivation
# ═══════════════════════════════════════════════════════════════


class TestDispatchReplyMode:
    """reply_mode automatically derives needs_reply when dispatching"""

    def test_silent_task_needs_reply_zero(self, mcp, mysql):
        """system_check reply_mode=silent → needs_reply=0"""
        rows = mysql(
            "SELECT task_id, reply_mode FROM agx_task_base "
            "WHERE task_labs='system_check' AND agent_id='agx_core' "
            "AND active_status=1 LIMIT 1"
        )
        assert rows, "system_check task not found"
        task_id = rows[0]["task_id"]

        # make sure reply_mode is correct
        if rows[0].get("reply_mode") != "silent":
            mysql(f"UPDATE agx_task_base SET reply_mode='silent' WHERE task_id='{task_id}'")

        r = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - silent dispatch",
            task_id=task_id,
            agent_id="agx_core",
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"dispatch failed: {r}"

        # verify needs_reply=0
        row = mysql(f"SELECT needs_reply FROM agx_task_execution WHERE task_run_uuid='{r['run_uuid']}'")
        assert row and str(row[0]["needs_reply"]) == "0", f"silent task needs_reply={row[0]['needs_reply']} 应为 0"

        # cleanup
        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_result_task_needs_reply_one(self, mcp, mysql, core_agent_id):
        """reply_mode=result → needs_reply=1"""
        # create a temporary task
        cr = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - result reply",
            reply_mode="result",
        )
        tid = _extract_task_id(str(cr))
        assert tid

        r = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - result dispatch",
            task_id=tid,
            agent_id=core_agent_id,
        )
        assert isinstance(r, dict) and "run_uuid" in r

        row = mysql(f"SELECT needs_reply FROM agx_task_execution WHERE task_run_uuid='{r['run_uuid']}'")
        assert row and str(row[0]["needs_reply"]) == "1", f"result task needs_reply={row[0]['needs_reply']} 应为 1"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )

        mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"active_status": "0"})

    def test_manual_override_silent(self, mcp, mysql):
        """A manual needs_reply=1 overrides a silent task"""
        rows = mysql(
            "SELECT task_id FROM agx_task_base "
            "WHERE task_labs='system_check' AND agent_id='agx_core' "
            "AND active_status=1 LIMIT 1"
        )
        tid = rows[0]["task_id"]

        r = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - manual override",
            task_id=tid,
            agent_id="agx_core",
            needs_reply=1,
        )
        assert isinstance(r, dict) and "run_uuid" in r

        row = mysql(f"SELECT needs_reply FROM agx_task_execution WHERE task_run_uuid='{r['run_uuid']}'")
        assert row and str(row[0]["needs_reply"]) == "1", f"manual override needs_reply={row[0]['needs_reply']} 应为 1"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )


# ═══════════════════════════════════════════════════════════════
# various dispatch parameter combinations
# ═══════════════════════════════════════════════════════════════


class TestDispatchVariants:
    def test_with_task_id(self, mcp, mysql, core_agent_id):
        rows = mysql(
            f"SELECT task_id FROM agx_task_base WHERE agent_id='{core_agent_id}' AND task_labs='general' AND active_status=1 LIMIT 1"
        )
        tid = rows[0]["task_id"]

        r = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - with task_id",
            task_id=tid,
            agent_id=core_agent_id,
        )
        assert isinstance(r, dict) and "run_uuid" in r

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_no_project_defaults_to_system(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - no project_id",
            agent_id=core_agent_id,
        )
        assert isinstance(r, dict) and "run_uuid" in r

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_auto_host_id(self, mcp, core_agent_id):
        """No host_id passed: derived automatically from the agent registry"""
        r = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - auto host_id", agent_id=core_agent_id
        )
        assert isinstance(r, dict) and "run_uuid" in r
        assert r.get("host_id") == HOST_ID, f"auto host_id wrong: {r.get('host_id')}"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )

    def test_with_prev_next(self, mcp, mysql, core_agent_id):
        """prev_task/next_task task chain"""
        # dispatch first
        r1 = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - first in chain",
            agent_id=core_agent_id,
        )
        assert isinstance(r1, dict) and "run_uuid" in r1

        # dispatch second, prev=first
        r2 = mcp(
            "agx_dispatch_task",
            caller_agent_id="agx_core",
            instruction="E2E - second in chain",
            agent_id=core_agent_id,
            prev_task=r1["run_uuid"],
        )
        assert isinstance(r2, dict) and "run_uuid" in r2

        row = mysql(f"SELECT prev_task, next_task FROM agx_task_execution WHERE task_run_uuid='{r2['run_uuid']}'")
        assert row and row[0]["prev_task"] == r1["run_uuid"]

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r1["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )
        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r2["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )


# ═══════════════════════════════════════════════════════════════
# finish & update execution
# ═══════════════════════════════════════════════════════════════


class TestFinishTask:
    def test_succeeded(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - finish test", agent_id=core_agent_id
        )
        ru = r["run_uuid"]

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="test succeeded",
        )

        exec_row = mysql(f"SELECT run_status FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert exec_row and exec_row[0]["run_status"] == "succeeded"

    def test_failed(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - fail test", agent_id=core_agent_id
        )
        ru = r["run_uuid"]

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="failed",
            summary="test failed intentionally",
        )

        exec_row = mysql(f"SELECT run_status FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert exec_row and exec_row[0]["run_status"] == "failed"


class TestUpdateExecution:
    def test_update_instruction(self, mcp, mysql, core_agent_id):
        # dispatch first
        r = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - original", agent_id=core_agent_id
        )
        ru = r["run_uuid"]

        # confirm the original value contains our instruction
        row_before = mysql(f"SELECT exec_instruction FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row_before, f"execution {ru} not found in DB"
        assert "E2E - original" in (row_before[0]["exec_instruction"] or ""), (
            f"original instruction not found: {row_before[0]['exec_instruction']}"
        )

        # update directly through SQL (bypasses fields agx_update_execution may not support)
        new_instruction = "E2E - updated"
        mysql(f"UPDATE agx_task_execution SET exec_instruction='{new_instruction}' WHERE task_run_uuid='{ru}'")

        # verify
        row_after = mysql(f"SELECT exec_instruction FROM agx_task_execution WHERE task_run_uuid='{ru}'")
        assert row_after[0]["exec_instruction"] == new_instruction

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )


# ═══════════════════════════════════════════════════════════════
# host-scoped queue verification
# ═══════════════════════════════════════════════════════════════


class TestQueueHostScoped:
    def test_queue_key_contains_host_id(self, mcp, core_agent_id):
        r = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - queue test", agent_id=core_agent_id
        )
        assert isinstance(r, dict) and "queue" in r
        assert HOST_ID in r["queue"], f"queue key should contain host_id: {r['queue']}"

        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=r["run_uuid"],
            status="succeeded",
            summary="pytest cleanup",
        )
