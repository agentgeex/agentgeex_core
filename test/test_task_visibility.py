"""
Task visibility E2E tests
Verifies caller_agent_id filtering and role permissions for
agx_get_task, agx_list_by_network, agx_list_agent_tasks, agx_get_task_by_name.
"""

import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.task_visibility

from conftest import AID_HOST, ENVS, MCPORTER_BIN, NW, ROOT, STDIO, mysql_raw  # noqa: E402

# ★ 2026-11-13: the original implementation carried its own hard-coded connection string (plaintext password plus
#   a database named agx that does not exist), bypassing conftest test isolation entirely — this visibility
#   suite had in fact never really run. Everything now comes from conftest: stdio command, test config and credentials in one place.
#
# —————————————————————————— fixtures ——————————————————————————


@pytest.fixture(scope="module")
def mcp():
    """mcporter call wrapper (uses conftest STDIO + ENVS, pinned to agentgeex_test)"""

    def call(tool, **kw):
        args = [
            MCPORTER_BIN,
            "call",
            "--stdio",
            STDIO,
            "--raw-strings",
            "--output",
            "text",
            *ENVS,
            tool,
        ]
        for k, v in kw.items():
            if isinstance(v, dict):
                args.append(f"{k}={json.dumps(v)}")
            else:
                args.append(f"{k}={v}")
        r = subprocess.run(args, capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        txt = r.stdout.strip()
        if not txt:
            return None
        if txt.startswith("{") or txt.startswith("["):
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                return txt
        return txt

    return call


@pytest.fixture(scope="module")
def mysql():
    """Read/write the test database (agentgeex_test), reusing conftest.mysql_raw"""

    def query(sql: str):
        try:
            return mysql_raw(sql)
        except Exception:  # noqa: BLE001
            return []

    return query


@pytest.fixture(scope="module")
def sample_task(mcp, mysql):
    """Pick a worker private task for testing"""
    r = mcp("agx_get_task_by_name", caller_agent_id="agx_core", agent_id="py_architect", task_name="task_flask_dev")
    assert isinstance(r, dict) and "task_id" in r, f"未找到测试 task: {r}"
    tid = r["task_id"]
    owner = "py_architect"
    # ensure it starts out private
    mysql(f"UPDATE agx_task_base SET is_private=1 WHERE task_id='{tid}'")
    yield {"task_id": tid, "owner": owner}
    # restore to public
    mysql(f"UPDATE agx_task_base SET is_private=0 WHERE task_id='{tid}'")


# —————————— agx_get_task single-task permissions ——————————


class TestAoGetTask:
    """agx_get_task: single-task permission check"""

    def test_workshop_manager_see_private(self, mcp, sample_task):
        """workshop_manager can read a private task"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id="agx_core")
        assert isinstance(r, dict) and "error" not in r

    def test_owner_see_own_private(self, mcp, sample_task):
        """the owner can read its own private task"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id=sample_task["owner"])
        assert isinstance(r, dict) and "error" not in r

    def test_host_manager_see_same_host_private(self, mcp, sample_task):
        """host_manager can read private tasks of agents on the same host"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id=AID_HOST)
        assert isinstance(r, dict) and "error" not in r

    def test_agent_manager_see_system_member_private(self, mcp, sample_task):
        """agent_manager can read private tasks of system_project members"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id="agx_agentfather")
        assert isinstance(r, dict), f"应该是 dict: {r}"
        assert "error" not in r, f"agent_manager 应能查看 system_project 成员的私有 task: {r}"
        assert r.get("task_id") == sample_task["task_id"]

    def test_unrelated_project_manager_blocked(self, mcp, sample_task):
        """a pm that does not manage the owner project cannot read the private task"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id="agx_projectmother")
        assert isinstance(r, dict) and "error" in r

    def test_unrelated_worker_blocked(self, mcp, sample_task):
        """an unrelated worker cannot read someone else private task"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id="expo_developer")
        assert isinstance(r, dict) and "error" in r

    def test_public_visible_to_other_worker(self, mcp, mysql, sample_task):
        """a public task is visible to other workers in the list"""
        # ⚠️ agx_get_task has a Redis cache layer, so is_private changes are out of sync with the DB
        # test public visibility through agx_list_by_network (the list path goes through SQL + cache rebuild)
        mysql(f"UPDATE agx_task_base SET is_private=0 WHERE task_id='{sample_task['task_id']}'")
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id="expo_developer")
        # restore to private
        mysql(f"UPDATE agx_task_base SET is_private=1 WHERE task_id='{sample_task['task_id']}'")
        tids = {t["task_id"] for t in r}
        assert sample_task["task_id"] in tids, "公开 task 应对 worker 在列表中可见"

    def test_nonexistent_caller(self, mcp, sample_task):
        """a non-existent caller should raise an error"""
        r = mcp("agx_get_task", task_id=sample_task["task_id"], caller_agent_id="ghost_agent_nonexist")
        s = str(r).lower()
        assert "error" in s or "exist" in s or "permission" in s, f"不存在 agent 应报错: {r}"


# —————————— agx_list_by_network list filtering ——————————


class TestAoListByNetwork:
    """agx_list_by_network entity_type=task: list filtering"""

    def test_workshop_manager_see_all(self, mcp):
        """the workshop_manager list includes private tasks"""
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id="agx_core")
        assert isinstance(r, list)
        private = [t for t in r if t.get("is_private") == 1]
        assert len(private) > 0, "workshop_manager 应能看到私有 task"

    def test_host_manager_see_same_host(self, mcp):
        """the host_manager list includes private tasks of same-host agents"""
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id=AID_HOST)
        private = [t for t in r if t.get("is_private") == 1]
        assert len(private) > 0, "host_manager 应能看到同主机私有 task"

    def test_agent_manager_see_system_member(self, mcp):
        """the agent_manager list includes private tasks of system_project members"""
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id="agx_agentfather")
        private = [t for t in r if t.get("is_private") == 1]
        assert len(private) > 0, "agent_manager 应能看到 system_project 成员私有 task"

    def test_project_manager_see_system_member(self, mcp):
        """the project_manager list includes private tasks of system_project members"""
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id="agx_projectmother")
        private = [t for t in r if t.get("is_private") == 1]
        assert len(private) > 0, "project_manager 应能看到 system_project 成员私有 task"

    def test_owner_see_own_private(self, mcp, sample_task):
        """the owner list includes its own private task"""
        r = mcp("agx_list_by_network", network_id=NW, entity_type="task", caller_agent_id=sample_task["owner"])
        tids = {t["task_id"] for t in r}
        assert sample_task["task_id"] in tids, f"owner 列表应含自己的私有 task {sample_task['task_id']}"
        # must not contain other people's private tasks
        others = [t for t in r if t.get("is_private") == 1 and t["task_id"] != sample_task["task_id"]]
        assert len(others) == 0, f"worker 不应看到别人的私有 task: {others}"

    def test_unrelated_project_manager_no_member_private(self, mcp, mysql, sample_task):
        """an unrelated pm list excludes private tasks of non-system-project members (needs a multi-project environment)"""
        pytest.skip("需要多项目环境才能验证非系统项目隔离")


# —————————— agx_list_agent_tasks cross-agent listing ——————————


class TestAoListAgentTasks:
    """agx_list_agent_tasks: the task list of a given agent"""

    def test_worker_see_others_public_only(self, mcp, sample_task):
        """a worker looking at another agent's tasks sees only the public ones"""
        r = mcp("agx_list_agent_tasks", caller_agent_id="expo_developer", agent_id=sample_task["owner"])
        assert isinstance(r, list)
        private = [t for t in r if t.get("is_private") == 1]
        assert len(private) == 0, f"worker 不应看到别人 agent 的私有 task: {private}"

    def test_owner_see_all_own(self, mcp, sample_task):
        """the owner looking at its own agent sees private tasks too"""
        r = mcp("agx_list_agent_tasks", caller_agent_id=sample_task["owner"], agent_id=sample_task["owner"])
        tids = {t["task_id"] for t in r}
        assert sample_task["task_id"] in tids, "owner 列表应含自己私有 task"


# —————————— agx_get_task_by_name lookup by name ——————————


class TestAoGetTaskByName:
    """agx_get_task_by_name: query by task_name, subject to visibility limits"""

    def test_owner_can_find(self, mcp, sample_task):
        """the owner can look up its own private task"""
        r = mcp(
            "agx_get_task_by_name",
            caller_agent_id=sample_task["owner"],
            agent_id=sample_task["owner"],
            task_name="task_flask_dev",
        )
        assert isinstance(r, dict) and "task_id" in r

    def test_other_worker_not_found(self, mcp, mysql, sample_task):
        """other workers cannot look up someone else private task"""
        mysql(f"UPDATE agx_task_base SET is_private=1 WHERE task_id='{sample_task['task_id']}'")
        r = mcp(
            "agx_get_task_by_name",
            caller_agent_id="expo_developer",
            agent_id=sample_task["owner"],
            task_name="task_flask_dev",
        )
        assert isinstance(r, dict) and "error" in r

    def test_agent_manager_can_find(self, mcp, sample_task):
        """agent_manager can look up private tasks of system_project members"""
        r = mcp(
            "agx_get_task_by_name",
            caller_agent_id="agx_agentfather",
            agent_id=sample_task["owner"],
            task_name="task_flask_dev",
        )
        assert isinstance(r, dict) and "task_id" in r, f"agent_manager 应能找到: {r}"
