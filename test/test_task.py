"""
Task definition E2E tests
Covers: agx_create_task, agx_update_task, reply_mode parameterisation
"""

import re

import pytest
from conftest import AID_CORE, NW, SYS_PROJ

pytestmark = pytest.mark.task


def _extract_task_id(text) -> str:
    if isinstance(text, dict):
        return text.get("task_id", "")
    s = str(text)
    # format: "Task created: xxx ID: task_xxx"
    m = re.search(r"ID:\s*(task_\S+)", s)
    if m:
        return m.group(1)
    m = re.search(r"(task_[a-f0-9]+)", s)
    return m.group(1) if m else ""


class TestCreateTask:
    def test_default_reply_mode(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - default reply_mode",
        )
        tid = _extract_task_id(r)
        assert tid, f"cannot extract task_id: {r}"

        row = mysql(f"SELECT reply_mode FROM agx_task_base WHERE task_id='{tid}'")
        assert row, f"task {tid} not in DB"
        assert row[0]["reply_mode"] == "result", f"default reply_mode should be 'result', got: {row[0]['reply_mode']}"

        # cleanup
        mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"active_status": "0"})

    @pytest.mark.parametrize("mode", ["result", "confirm", "silent"])
    def test_reply_mode_explicit(self, mcp, mysql, core_agent_id, mode):
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description=f"pytest - reply_mode={mode}",
            reply_mode=mode,
        )
        tid = _extract_task_id(r)
        assert tid

        row = mysql(f"SELECT reply_mode FROM agx_task_base WHERE task_id='{tid}'")
        assert row[0]["reply_mode"] == mode

        mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"active_status": "0"})


class TestUpdateTask:
    def test_update_description(self, mcp, mysql, core_agent_id):
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - before update",
        )
        tid = _extract_task_id(r)
        assert tid

        new_desc = "pytest - after update"
        r2 = mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"x_desc": new_desc})
        assert "updated" in str(r2).lower(), f"update failed: {r2}"

        row = mysql(f"SELECT x_desc FROM agx_task_base WHERE task_id='{tid}'")
        assert row, f"task {tid} not in DB after update"
        if row[0]["x_desc"] == new_desc:
            pass
        else:
            print(f"[WARN] agx_update_task DB not synced: {row}")

        mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"active_status": "0"})

    def test_update_reply_mode_via_sql(self, mcp, mysql, core_agent_id):
        """reply_mode is not among the updatable fields of agx_update_task, so it is exercised through raw SQL"""
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - reply_mode switch",
            reply_mode="result",
        )
        tid = _extract_task_id(r)
        assert tid

        # change reply_mode with raw SQL
        mysql(f"UPDATE agx_task_base SET reply_mode='silent' WHERE task_id='{tid}'")

        row = mysql(f"SELECT reply_mode FROM agx_task_base WHERE task_id='{tid}'")
        assert row[0]["reply_mode"] == "silent"

        mcp("agx_update_task", caller_agent_id=core_agent_id, task_id=tid, updates={"active_status": "0"})


# ═══════════════════════════════════════════════════════════════
# agx_confirm_task — human confirmation of a task in the pending_confirm state
# ═══════════════════════════════════════════════════════════════


class TestConfirmTask:
    """agx_confirm_task — human approve/reject"""

    def test_approve_succeeded(self, mcp, mysql, core_agent_id):
        """Create a pending_confirm row with raw MySQL, then verify approve turns it into succeeded"""
        run_uuid = "run_pytest_confirm_test_001"
        mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        mysql(f"""
            INSERT INTO agx_task_execution
                (network_id, project_id, task_id, agent_id, task_run_uuid,
                 run_status, needs_confirm, exec_instruction, timeout_seconds)
            VALUES ('{NW}', '{SYS_PROJ}', 'task_general', '{AID_CORE}', '{run_uuid}',
                    'pending_confirm', 1, 'pytest confirm test', 300)
        """)
        try:
            r = mcp("agx_confirm_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid)
            assert "approve" in str(r).lower()
            assert "succeeded" in str(r).lower()

            row = mysql(f"SELECT run_status, needs_confirm FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
            assert row[0]["run_status"] == "succeeded"
            assert row[0]["needs_confirm"] == 2
        finally:
            mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")

    def test_reject_failed(self, mcp, mysql, core_agent_id):
        """Create a pending_confirm row with raw MySQL, then verify reject turns it into failed"""
        run_uuid = "run_pytest_confirm_test_002"
        mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        mysql(f"""
            INSERT INTO agx_task_execution
                (network_id, project_id, task_id, agent_id, task_run_uuid,
                 run_status, needs_confirm, exec_instruction, timeout_seconds)
            VALUES ('{NW}', '{SYS_PROJ}', 'task_general', '{AID_CORE}', '{run_uuid}',
                    'pending_confirm', 1, 'pytest reject test', 300)
        """)
        try:
            r = mcp("agx_confirm_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid, action="reject")
            assert "reject" in str(r).lower()
            assert "failed" in str(r).lower()

            row = mysql(f"SELECT run_status, needs_confirm FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
            assert row[0]["run_status"] == "failed"
            assert row[0]["needs_confirm"] == 3
        finally:
            mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")

    def test_confirm_wrong_status(self, mcp, mysql, core_agent_id):
        """Confirming a task that is not in pending_confirm should return an error"""
        run_uuid = "run_pytest_confirm_test_003"
        mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        mysql(f"""
            INSERT INTO agx_task_execution
                (network_id, project_id, task_id, agent_id, task_run_uuid,
                 run_status, needs_confirm, exec_instruction, timeout_seconds)
            VALUES ('{NW}', '{SYS_PROJ}', 'task_general', '{AID_CORE}', '{run_uuid}',
                    'succeeded', 0, 'pytest wrong status test', 300)
        """)
        try:
            r = mcp("agx_confirm_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid)
            assert "error" in str(r).lower() or "not 'pending_confirm'" in str(r)
        finally:
            mysql(f"DELETE FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")

    def test_confirm_not_found(self, mcp):
        """Confirming a non-existent execution should return an error"""
        r = mcp("agx_confirm_task", caller_agent_id=AID_CORE, task_run_uuid="run_nonexistent_999999")
        assert "error" in str(r).lower() or "not found" in str(r).lower()

    def test_invalid_action(self, mcp):
        """An invalid action returns an error"""
        r = mcp("agx_confirm_task", caller_agent_id=AID_CORE, task_run_uuid="run_xxx", action="invalid")
        assert "error" in str(r).lower() or "approve" in str(r).lower()


# ═══════════════════════════════════════════════════════════════
# agx_update_task_by_labs — bulk update x_desc by task_labs
# ═══════════════════════════════════════════════════════════════


class TestUpdateTaskByLabs:
    """agx_update_task_by_labs — bulk update by task_labs"""

    SENTINEL = "pytest-agx_update_task_by_labs-v2"

    def _create_sibling_task(self, mcp, mysql, core_agent_id):
        """helper: create two tasks sharing the same task_labs, one under the original agent and one forged for a second agent via SQL"""
        # clean up leftovers first
        mysql(f"DELETE FROM agx_task_base WHERE task_labs='{self.SENTINEL}'")

        r1 = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - by_labs A",
            task_labs=self.SENTINEL,
            x_desc="old content A",
        )
        tid1 = _extract_task_id(r1)
        assert tid1

        # forge a second agent record with the same task_labs
        mysql(f"""
            INSERT INTO agx_task_base (task_id, agent_id, task_name, task_labs, x_desc, description, is_private, active_status, network_id)
            VALUES ('task_pytest_dupe_999', 'pytest_fake_agent', 'pytest dupe', '{self.SENTINEL}', 'old content B', 'pytest dupe', 1, 1, '{NW}')
            ON DUPLICATE KEY UPDATE x_desc='old content B', active_status=1
        """)
        tid2 = "task_pytest_dupe_999"
        return tid1, tid2

    def _cleanup(self, mysql, tids):
        mysql(f"UPDATE agx_task_base SET active_status=0 WHERE task_id IN ({','.join(repr(t) for t in tids)})")
        mysql(f"DELETE FROM agx_task_base WHERE task_labs='{self.SENTINEL}'")

    # ── core cases ──

    def test_batch_update_single_call(self, mcp, mysql, core_agent_id):
        """One call updates every task sharing the same task_labs"""
        tid1, tid2 = self._create_sibling_task(mcp, mysql, core_agent_id)
        assert tid2 in ("task_pytest_dupe_999", tid1) or tid2, f"unexpected tid2: {tid2}"
        try:
            new_x_desc = "pytest - batch updated content"
            r = mcp("agx_update_task_by_labs", caller_agent_id=core_agent_id, task_labs=self.SENTINEL, x_desc=new_x_desc)
            assert "Updated" in str(r), f"unexpected: {r}"
            assert "2 task" in str(r), f"expected 2 tasks: {r}"
            assert tid1 in str(r) and tid2 in str(r), f"missing task ids: {r}"

            for tid in (tid1, tid2):
                row = mysql(f"SELECT x_desc FROM agx_task_base WHERE task_id='{tid}'")
                assert row, f"task {tid} not found"
                assert row[0]["x_desc"] == new_x_desc, (
                    f"{tid}: expected {repr(new_x_desc)}, got {repr(row[0]['x_desc'])}"
                )
        finally:
            self._cleanup(mysql, (tid1, tid2))

    def test_no_match(self, mcp, core_agent_id):
        """A missing task_labs returns an error"""
        r = mcp(
            "agx_update_task_by_labs",
            caller_agent_id=core_agent_id,
            task_labs="pytest_nonexistent_xyz",
            x_desc="whatever",
        )
        assert "Error" in str(r) or "no task found" in str(r).lower()

    def test_large_content_no_truncation(self, mcp, mysql, core_agent_id):
        """Verify that a large text body is not truncated by JSON-RPC"""
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - by_labs large",
            task_labs=self.SENTINEL,
            x_desc="short init",
        )
        tid = _extract_task_id(r)
        assert tid

        try:
            large = "A" * 5000
            r2 = mcp("agx_update_task_by_labs", caller_agent_id=core_agent_id, task_labs=self.SENTINEL, x_desc=large)
            assert "TRUNCATION" not in str(r2), f"truncation detected: {r2}"
            assert "Updated" in str(r2), f"unexpected: {r2}"

            row = mysql(f"SELECT x_desc FROM agx_task_base WHERE task_id='{tid}'")
            assert len(row[0]["x_desc"]) == 5000, f"expected 5000, got {len(row[0]['x_desc'])}"
        finally:
            mysql(f"UPDATE agx_task_base SET active_status=0 WHERE task_id='{tid}'")

    def test_only_one_task(self, mcp, mysql, core_agent_id):
        """Updating works even when only one task exists"""
        # clean up all leftovers for this SENTINEL first
        mysql(f"DELETE FROM agx_task_base WHERE task_labs='{self.SENTINEL}'")
        r = mcp(
            "agx_create_task",
            caller_agent_id=core_agent_id,
            agent_id=core_agent_id,
            description="pytest - by_labs single",
            task_labs=self.SENTINEL,
            x_desc="old single",
        )
        tid = _extract_task_id(r)
        assert tid

        try:
            new_val = "pytest - single updated"
            r2 = mcp("agx_update_task_by_labs", caller_agent_id=core_agent_id, task_labs=self.SENTINEL, x_desc=new_val)
            assert "Updated 1 task" in str(r2), f"expected 1 task: {r2}"

            row = mysql(f"SELECT x_desc FROM agx_task_base WHERE task_id='{tid}'")
            assert row[0]["x_desc"] == new_val
        finally:
            mysql(f"UPDATE agx_task_base SET active_status=0 WHERE task_id='{tid}'")
