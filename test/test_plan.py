"""
JSON plan deployment and cancellation E2E tests
Covers: agx_plan_deploy, agx_plan_query, agx_plan_list, agx_plan_cancel

v2: plan_json became plan_path (the path to a JSON file, read by MCP itself).
"""

import json
import os
import re
import tempfile

import pytest

pytestmark = pytest.mark.plan


def _extract_plan_id(text: str) -> str:
    m = re.search(r"(plan_[a-f0-9]+)", text)
    return m.group(1) if m else ""


def _write_plan_file(plan: dict) -> str:
    """Write a plan dict to a temporary JSON file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="plan_test_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    return path


class TestPlanDeploy:
    def test_deploy_and_query(self, mcp, mysql, core_agent_id):
        plan = {
            "plan_id": "plan_pytest_e2e_001",
            "project_id": "system_project",
            "description": "pytest plan deploy test",
            "steps": [
                {
                    "step": 1,
                    "agent_id": core_agent_id,
                    "instruction": "pytest plan step 1",
                    "children": [
                        {
                            "step": "1.1",
                            "agent_id": core_agent_id,
                            "instruction": "pytest plan sub-step 1.1",
                        },
                        {
                            "step": "1.2",
                            "agent_id": core_agent_id,
                            "instruction": "pytest plan sub-step 1.2",
                        },
                    ],
                },
                {
                    "step": 2,
                    "agent_id": core_agent_id,
                    "instruction": "pytest plan step 2",
                },
            ],
        }
        plan_path = _write_plan_file(plan)
        try:
            r = mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_path=plan_path)
            msg = str(r)
            pid = _extract_plan_id(msg)
            assert pid or "plan" in msg.lower(), f"plan deploy failed: {msg}"

            # query the plan
            qr = mcp("agx_plan_query", caller_agent_id="agx_core", plan_id="plan_pytest_e2e_001", limit=20)
            assert isinstance(qr, dict), f"plan query failed: {qr}"

            # cancel
            mcp("agx_plan_cancel", caller_agent_id="agx_core", plan_id="plan_pytest_e2e_001")

            # query again — the status should now be cancelled
            qr2 = mcp("agx_plan_query", caller_agent_id="agx_core", plan_id="plan_pytest_e2e_001", limit=20)
            assert isinstance(qr2, dict)
        finally:
            os.unlink(plan_path)

    def test_list_plans(self, mcp):
        r = mcp("agx_plan_list", caller_agent_id="agx_core", project_id="system_project")
        assert r is not None

    def test_invalid_input_rejected(self, mcp):
        """An invalid JSON file body should be rejected"""
        plan_path = _write_plan_file({"invalid": "no plan_id"})
        try:
            r = mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_path=plan_path)
            msg = str(r)
            assert "error" in msg.lower() or "missing" in msg.lower(), f"invalid plan should be rejected: {msg}"
        finally:
            os.unlink(plan_path)

    def test_mutual_exclusion_prev_and_parent(self, mcp):
        """prev_task and parent_run_uuid are mutually exclusive"""
        plan = {
            "plan_id": "plan_pytest_mutex_002",
            "project_id": "system_project",
            "description": "互斥校验测试",
            "steps": [
                {
                    "run_uuid": "run_mutex_parent_002",
                    "task_id": "",
                    "agent_id": "agx_core",
                    "instruction": "parent step",
                    "children": ["run_mutex_child_002"],
                },
                {
                    "run_uuid": "run_mutex_child_002",
                    "task_id": "",
                    "agent_id": "agx_core",
                    "instruction": "child with prev_task",
                    "parent_run_uuid": "run_mutex_parent_002",
                    "prev_task": "run_mutex_parent_002",  # <- carries both parent_run_uuid and prev_task
                },
            ],
        }
        plan_path = _write_plan_file(plan)
        try:
            r = mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_path=plan_path)
            msg = str(r)
            assert "互斥" in msg, f"should reject prev+parent: {msg}"
        finally:
            os.unlink(plan_path)

    def test_chain_mode_create_then_deploy(self, mcp, mysql, core_agent_id):
        """Chain mode: agx_plan_create(plan_path=...) -> agx_plan_deploy(plan_id=...)"""
        import uuid

        suffix = uuid.uuid4().hex[:8]
        plan_id = f"plan_pytest_chain_{suffix}"
        run1 = f"run_chain_s1_{suffix}"
        run2 = f"run_chain_s2_{suffix}"
        plan = {
            "plan_id": plan_id,
            "project_id": "system_project",
            "description": "链式模式测试",
            "steps": [
                {
                    "run_uuid": run1,
                    "task_id": "",
                    "agent_id": core_agent_id,
                    "instruction": "chain step 1",
                },
                {
                    "run_uuid": run2,
                    "task_id": "",
                    "agent_id": core_agent_id,
                    "instruction": "chain step 2",
                },
            ],
        }

        plan_path = _write_plan_file(plan)
        try:
            # Step 1: create (pass the file directly)
            r1 = mcp("agx_plan_create", caller_agent_id="agx_core", plan_path=plan_path)
            d1 = r1 if isinstance(r1, dict) else json.loads(str(r1))
            assert d1.get("plan_id") == plan_id, f"create failed: {r1}"
            assert d1.get("plan_status") == "draft", f"expected draft: {r1}"

            # Step 2: deploy by plan_id
            r2 = mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_id=plan_id)
            d2 = r2 if isinstance(r2, dict) else json.loads(str(r2))
            assert d2.get("ok") is True, f"deploy failed: {r2}"
            assert d2.get("steps_deployed") == 2, f"expected 2 steps: {r2}"

            # cleanup
            mcp("agx_plan_cancel", caller_agent_id="agx_core", plan_id=plan_id)
        finally:
            os.unlink(plan_path)


class TestActivateMode:
    """activate_mode + start_task_run_uuid"""

    def test_watchdog_mode_default(self, mcp, mysql, core_agent_id):
        """Default watchdog mode: agx_activate_plan moves every draft to pending"""
        import uuid
        suffix = uuid.uuid4().hex[:8]
        plan_id = f"plan_pytest_wd_{suffix}"
        plan = {
            "plan_id": plan_id,
            "project_id": "system_project",
            "description": "watchdog 模式测试（默认）",
            "steps": [
                {"run_uuid": f"run_wd_s1_{suffix}", "agent_id": core_agent_id, "instruction": "wd step 1"},
                {"run_uuid": f"run_wd_s2_{suffix}", "agent_id": core_agent_id, "instruction": "wd step 2"},
            ],
        }
        plan_path = _write_plan_file(plan)
        try:
            # create + deploy
            mcp("agx_plan_create", caller_agent_id="agx_core", plan_path=plan_path)
            mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_id=plan_id)
            # activate (watchdog by default)
            r = mcp("agx_activate_plan", caller_agent_id="agx_core", plan_id=plan_id)
            d = r if isinstance(r, dict) else json.loads(str(r))
            assert d.get("activate_mode") == "watchdog", f"expected watchdog: {r}"
            assert d.get("activated_count") == 2, f"expected 2 steps activated: {r}"
            # verify: everything is pending
            rows = mysql(
                f"SELECT run_status FROM agx_task_execution WHERE plan_id = '{plan_id}'"
            )
            for row in rows:
                assert row["run_status"] == "pending", f"step should be pending, got {row}"
            # cleanup
            mcp("agx_plan_cancel", caller_agent_id="agx_core", plan_id=plan_id)
        finally:
            os.unlink(plan_path)

    def test_chain_mode_activate_entry_only(self, mcp, mysql, core_agent_id):
        """Chain mode: only start_task_run_uuid is activated (draft -> running); the rest stay draft"""
        import uuid
        suffix = uuid.uuid4().hex[:8]
        plan_id = f"plan_pytest_chain_{suffix}"
        entry_run = f"run_entry_{suffix}"
        plan = {
            "plan_id": plan_id,
            "project_id": "system_project",
            "description": "链式模式测试",
            "activate_mode": "chain",
            "start_task_run_uuid": entry_run,
            "steps": [
                {"run_uuid": entry_run, "agent_id": core_agent_id, "instruction": "chain entry"},
                {"run_uuid": f"run_step2_{suffix}", "agent_id": core_agent_id, "instruction": "chain step 2"},
                {"run_uuid": f"run_step3_{suffix}", "agent_id": core_agent_id, "instruction": "chain step 3"},
            ],
        }
        plan_path = _write_plan_file(plan)
        try:
            # create + deploy
            mcp("agx_plan_create", caller_agent_id="agx_core", plan_path=plan_path)
            mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_id=plan_id)
            # activate
            r = mcp("agx_activate_plan", caller_agent_id="agx_core", plan_id=plan_id)
            d = r if isinstance(r, dict) else json.loads(str(r))
            assert d.get("activate_mode") == "chain", f"expected chain: {r}"
            assert d.get("activated_count") == 1, f"only entry should be activated: {r}"
            # verify: the entry point is running, the rest stay draft
            rows = mysql(
                f"SELECT task_run_uuid, run_status FROM agx_task_execution WHERE plan_id = '{plan_id}'"
            )
            for row in rows:
                if row["task_run_uuid"] == entry_run:
                    assert row["run_status"] == "running", f"entry should be running: {row}"
                else:
                    assert row["run_status"] == "draft", f"non-entry should be draft: {row}"
            # cleanup
            mcp("agx_plan_cancel", caller_agent_id="agx_core", plan_id=plan_id)
        finally:
            os.unlink(plan_path)

    def test_chain_mode_missing_start_uuid_rejected(self, mcp, mysql, core_agent_id):
        """Chain mode without start_task_run_uuid should raise an error"""
        import uuid
        suffix = uuid.uuid4().hex[:8]
        plan_id = f"plan_pytest_chain_bad_{suffix}"
        plan = {
            "plan_id": plan_id,
            "project_id": "system_project",
            "description": "链式但没 start_task_run_uuid",
            "activate_mode": "chain",
            "steps": [
                {"run_uuid": f"run_x1_{suffix}", "agent_id": core_agent_id, "instruction": "step 1"},
            ],
        }
        plan_path = _write_plan_file(plan)
        try:
            mcp("agx_plan_create", caller_agent_id="agx_core", plan_path=plan_path)
            mcp("agx_plan_deploy", caller_agent_id="agx_core", plan_id=plan_id)
            r = mcp("agx_activate_plan", caller_agent_id="agx_core", plan_id=plan_id)
            msg = str(r)
            assert "start_task_run_uuid" in msg.lower() and "error" in msg.lower(), f"should reject missing start: {msg}"
            # cleanup
            mcp("agx_plan_cancel", caller_agent_id="agx_core", plan_id=plan_id)
        finally:
            os.unlink(plan_path)


class TestHostManager:
    """agx_resolve_host_manager"""

    def test_resolve_local(self, mcp, host_id):
        r = mcp("agx_resolve_host_manager", caller_agent_id="agx_core", host_id=host_id)
        data = r if isinstance(r, dict) else {}
        assert data.get("host_id") == host_id or "agent_id" in data, f"resolve failed: {data}"

    def test_resolve_unknown(self, mcp):
        r = mcp("agx_resolve_host_manager", caller_agent_id="agx_core", host_id="host_nonexistent_999")
        data = r if isinstance(r, dict) else {}
        assert "error" in data or "host" in str(data).lower(), f"unknown host should error: {data}"
