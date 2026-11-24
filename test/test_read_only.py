"""
Read-only MCP tool E2E tests
Covers: agx_list_by_network, agx_list_agent_tasks, agx_get_*, agx_check_services, agx_cache_stats, agx_generate_uuid
"""

import json

import pytest

pytestmark = pytest.mark.read_only


# ═══════════════════════════════════════════════════════════════
# 1.1 agx_list_by_network
# ═══════════════════════════════════════════════════════════════


class TestListByNetwork:
    """agx_list_by_network — list entities of each type by network"""

    @pytest.mark.parametrize(
        "entity_type,expected_keys",
        [
            ("project", ["project_id", "project_name"]),
            ("host", ["host_id", "host_alias"]),
            ("agent", ["agent_id", "agent_name", "agent_role"]),
            ("task", ["task_id", "task_name", "task_labs"]),
        ],
    )
    def test_list_by_type(self, mcp, network_id, entity_type, expected_keys):
        r = mcp("agx_list_by_network", caller_agent_id="agx_core", network_id=network_id, entity_type=entity_type)
        assert isinstance(r, list), f"expected list, got {type(r).__name__}: {r}"
        assert len(r) > 0, f"no {entity_type} entities found"
        for key in expected_keys:
            assert key in r[0], f"missing key '{key}' in {entity_type} result"

    def test_list_invalid_type(self, mcp, network_id):
        r = mcp("agx_list_by_network", caller_agent_id="agx_core", network_id=network_id, entity_type="not_a_type")
        r_str = str(r)
        assert "error" in r_str.lower() or "Invalid" in r_str, f"unexpected: {r}"

    def test_list_task_by_agent_id(self, mcp, network_id, core_agent_id):
        """agx_list_by_network(entity_type="task", agent_id=xxx) — filter tasks by agent"""
        r = mcp(
            "agx_list_by_network",
            caller_agent_id="agx_core",
            network_id=network_id,
            entity_type="task",
            agent_id=core_agent_id,
        )
        assert isinstance(r, list), f"expected list, got {type(r).__name__}: {r}"
        assert len(r) > 0, f"no tasks found for {core_agent_id}"
        # every returned task must belong to core_agent_id
        for t in r:
            assert t.get("agent_id") == core_agent_id, (
                f"agent_id filter failed: expected {core_agent_id}, got {t.get('agent_id')}"
            )

    def test_list_task_no_agent_filter(self, mcp, network_id):
        """agx_list_by_network(entity_type="task") without agent_id — returns everything"""
        r_all = mcp("agx_list_by_network", caller_agent_id="agx_core", network_id=network_id, entity_type="task")
        r_filtered = mcp(
            "agx_list_by_network",
            caller_agent_id="agx_core",
            network_id=network_id,
            entity_type="task",
            agent_id="agx_core",
        )
        assert isinstance(r_all, list)
        assert isinstance(r_filtered, list)
        # total >= filtered
        assert len(r_all) >= len(r_filtered), f"all({len(r_all)}) < filtered({len(r_filtered)})"


# ═══════════════════════════════════════════════════════════════
# 1.1b agx_list_agent_tasks — exact lookup of an agent's task list
# ═══════════════════════════════════════════════════════════════


class TestListAgentTasks:
    """agx_list_agent_tasks — look up tasks by exact agent_id, never truncated"""

    def test_list_core_tasks(self, mcp, core_agent_id):
        r = mcp("agx_list_agent_tasks", caller_agent_id="agx_core", agent_id=core_agent_id)
        assert isinstance(r, list), f"expected list, got {type(r).__name__}: {r}"
        assert len(r) > 0, f"no tasks for {core_agent_id}"
        for key in ["task_id", "task_name", "task_labs", "agent_id"]:
            assert key in r[0], f"missing key '{key}' in result"
        # every task must belong to core_agent_id
        for t in r:
            assert t.get("agent_id") == core_agent_id, f"expected {core_agent_id}, got {t.get('agent_id')}"

    def test_list_host_tasks(self, mcp, host_agent_id):
        """Verify the host agent task list contains agent_deploy"""
        r = mcp("agx_list_agent_tasks", caller_agent_id=host_agent_id, agent_id=host_agent_id)
        assert isinstance(r, list), f"expected list, got {type(r).__name__}: {r}"
        assert len(r) > 0, f"no tasks for {host_agent_id}"
        # the host agent must have the agent_deploy task
        deploy_tasks = [t for t in r if t.get("task_labs") == "agent_deploy"]
        assert len(deploy_tasks) > 0, (
            f"agent_deploy task not found in {host_agent_id} tasks: {[t.get('task_labs') for t in r]}"
        )

    def test_list_agentfather_tasks(self, mcp):
        """Verify the agentfather tasks include agent_create and agent_clone"""
        r = mcp("agx_list_agent_tasks", caller_agent_id="agx_core", agent_id="agx_agentfather")
        assert isinstance(r, list)
        assert len(r) > 0
        labs = {t.get("task_labs") for t in r}
        assert "agent_create" in labs, f"agent_create not in {labs}"
        assert "agent_clone" in labs, f"agent_clone not in {labs}"

    def test_list_unknown_agent(self, mcp):
        """A non-existent agent returns an empty list (should not raise)"""
        r = mcp("agx_list_agent_tasks", caller_agent_id="agx_core", agent_id="agent_nonexistent_99999")
        assert isinstance(r, list), f"expected list, got {type(r).__name__}: {r}"
        assert len(r) == 0, f"expected empty for unknown agent, got {len(r)}: {r}"


# ═══════════════════════════════════════════════════════════════
# 1.2 agx_check_services + agx_cache_stats
# ═══════════════════════════════════════════════════════════════


class TestServices:
    """System self-check and cache statistics"""

    def test_check_services(self, mcp):
        r = mcp("agx_check_services", caller_agent_id="agx_core")
        if isinstance(r, dict):
            # structuredContent format
            health = str(r)
            assert any(k in health for k in ["mysql", "redis", "MySQL", "Redis"]), f"unexpected: {r}"
        else:
            assert isinstance(r, str), f"unexpected type: {type(r)}"
            assert "MySQL" in r and "Redis" in r, f"check_services: {r}"

    def test_cache_stats(self, mcp):
        r = mcp("agx_cache_stats", caller_agent_id="agx_core")
        assert isinstance(r, dict), f"expected dict: {r}"
        stat_str = str(r)
        assert any(k in stat_str for k in ["project", "host", "agent", "task", "execution"]), (
            f"unexpected cache_stats: {r}"
        )


# ═══════════════════════════════════════════════════════════════
# 1.3 agx_generate_uuid
# ═══════════════════════════════════════════════════════════════


class TestGenerateUUID:
    """UUID generator"""

    @pytest.mark.parametrize("prefix", ["proj", "task", "run", "agt", "host", "sess"])
    def test_valid_prefix(self, mcp, prefix):
        r = mcp("agx_generate_uuid", caller_agent_id="agx_core", prefix=prefix)
        uid = str(r) if isinstance(r, dict) else r
        assert uid.startswith(f"{prefix}_"), f"expected '{prefix}_*', got '{uid}'"
        assert len(uid) > len(prefix) + 5, f"too short: {uid}"

    def test_with_infix(self, mcp):
        r = mcp("agx_generate_uuid", caller_agent_id="agx_core", prefix="run", infix="test_lab")
        uid = str(r) if isinstance(r, dict) else r
        assert uid.startswith("run_test_lab_"), f"expected 'run_test_lab_*', got '{uid}'"

    def test_default_prefix(self, mcp):
        r = mcp("agx_generate_uuid", caller_agent_id="agx_core")
        uid = str(r) if isinstance(r, dict) else r
        assert isinstance(uid, str) and len(uid) > 5


# ═══════════════════════════════════════════════════════════════
# 1.4-1.7 agx_get_*
# ═══════════════════════════════════════════════════════════════


class TestGetProject:
    def test_system_project(self, mcp, system_project):
        r = mcp("agx_get_project", caller_agent_id="agx_core", project_id=system_project)
        data = r if isinstance(r, dict) else {}
        assert data.get("project_id") == system_project, f"unexpected: {data}"
        assert data.get("active_status") == "1"

    def test_nonexistent(self, mcp):
        r = mcp("agx_get_project", caller_agent_id="agx_core", project_id="proj_nonexistent_999")
        r_str = str(r)
        assert "error" in r_str.lower() or r is None, f"unexpected: {r}"


class TestGetHost:
    def test_local_host(self, mcp, host_id):
        r = mcp("agx_get_host", caller_agent_id="agx_core", host_id=host_id)
        data = r if isinstance(r, dict) else {}
        assert data.get("host_id") == host_id, f"unexpected: {data}"


class TestGetAgent:
    def test_core_agent(self, mcp, core_agent_id):
        r = mcp("agx_get_agent", caller_agent_id="agx_core", agent_id=core_agent_id)
        data = r if isinstance(r, dict) else {}
        assert data.get("agent_id") == core_agent_id
        assert "agent_role" in data
        assert "active_status" in data
        assert "host_id" in data


class TestGetTask:
    def test_system_check_task(self, mcp, mysql):
        """Verify the system_check task exists with complete fields"""
        rows = mysql(
            "SELECT task_id FROM agx_task_base "
            "WHERE task_labs='system_check' AND agent_id='agx_core' "
            "AND active_status=1 LIMIT 1"
        )
        assert rows, "system_check task not found"
        task_id = rows[0]["task_id"]

        r = mcp("agx_get_task", caller_agent_id="agx_core", task_id=task_id)
        data = r if isinstance(r, dict) else {}
        assert data.get("task_id") == task_id
        assert data.get("task_labs") == "system_check"


class TestGetTaskByName:
    """agx_get_task_by_name — look up task_id by its stable task_name"""

    def test_get_deploy_task(self, mcp, core_agent_id, host_agent_id):
        """Look up the host agent deploy task with task_name='agent_deploy'"""
        r = mcp("agx_get_task_by_name", caller_agent_id="agx_core", agent_id=host_agent_id, task_name="agent_deploy")
        data = r if isinstance(r, dict) else json.loads(r)
        assert "task_id" in data, f"should find task, got: {r}"
        assert data["task_id"].startswith("task_"), f"invalid task_id: {data.get('task_id')}"
        assert data.get("task_labs") == "agent_deploy", f"task_labs mismatch: {data.get('task_labs')}"
        assert data["agent_id"] == host_agent_id, f"agent_id mismatch: {data.get('agent_id')}"

    def test_get_agentfather_create_task(self, mcp, agentfather_id):
        """Look up the agentfather create task with task_name='agent_create'"""
        r = mcp(
            "agx_get_task_by_name", caller_agent_id="agx_core", agent_id=agentfather_id, task_name="agent_create"
        )
        data = r if isinstance(r, dict) else json.loads(r)
        assert "task_id" in data, f"should find task, got: {r}"
        assert data.get("task_labs") == "agent_create"
        assert data["agent_id"] == agentfather_id
        assert "task_labs" in data

    def test_task_not_found(self, mcp, core_agent_id):
        """A non-existent task_name returns an error"""
        r = mcp(
            "agx_get_task_by_name",
            caller_agent_id="agx_core",
            agent_id=core_agent_id,
            task_name="nonexistent_task_xyz",
        )
        data = r if isinstance(r, dict) else json.loads(r)
        assert "error" in data or "not found" in str(r).lower(), f"should return error for unknown task, got: {r}"

    def test_unknown_agent(self, mcp):
        """A non-existent agent_id returns an error"""
        r = mcp(
            "agx_get_task_by_name", caller_agent_id="agx_core", agent_id="nonexistent_agent_xyz", task_name="general"
        )
        data = r if isinstance(r, dict) else json.loads(r)
        assert "error" in data or "not found" in str(r).lower(), f"should return error for unknown agent, got: {r}"
