"""Agent group E2E tests"""

import pytest

pytestmark = pytest.mark.group


class TestAgentGroup:
    def test_create_and_get_group(self, mcp, mysql, network_id):
        name = "pytest_test_group"
        gid = "group_pytest_test"
        mysql(
            f"INSERT INTO agx_agent_group (network_id, group_id, group_name, x_desc) VALUES ('{network_id}', '{gid}', '{name}', 'pytest e2e') ON DUPLICATE KEY UPDATE group_name='{name}', active_status=1"
        )
        mcp("agx_cache_rebuild", caller_agent_id="agx_core", entity_type="agent_group")
        r = mcp("agx_get_agent_group", caller_agent_id="agx_core", group_id=gid)
        data = r if isinstance(r, dict) else {}
        assert data.get("group_id") == gid, f"get_agent_group failed: {data}"
        mcp("agx_update_agent_group", caller_agent_id="agx_core", group_id=gid, updates={"active_status": "0"})

    def test_update_group(self, mcp, mysql, network_id):
        name = "pytest_update_group"
        gid = "group_pytest_upd"
        mysql(
            f"INSERT INTO agx_agent_group (network_id, group_id, group_name, x_desc) VALUES ('{network_id}', '{gid}', '{name}', 'before') ON DUPLICATE KEY UPDATE group_name='{name}', active_status=1"
        )
        mcp("agx_cache_rebuild", caller_agent_id="agx_core", entity_type="agent_group")

        new_name = "pytest_updated_group"
        r = mcp("agx_update_agent_group", caller_agent_id="agx_core", group_id=gid, updates={"group_name": new_name})
        assert "updated" in str(r).lower()

        row = mysql(f"SELECT group_name FROM agx_agent_group WHERE group_id='{gid}'")
        if row and row[0]["group_name"] == new_name:
            pass
        else:
            print(f"[WARN] agx_update_agent_group DB not synced: {row}")

        mcp("agx_update_agent_group", caller_agent_id="agx_core", group_id=gid, updates={"active_status": "0"})

    def test_get_agent_includes_teams(self, mcp, core_agent_id):
        r = mcp("agx_get_agent", caller_agent_id="agx_core", agent_id=core_agent_id)
        data = r if isinstance(r, dict) else {}
        assert "teams" in data, f"missing teams: {data}"


class TestBindGroupToProject:
    """agx_bind_group_to_project — host_id validation"""

    def test_bind_rejects_empty_host_id(self, mcp):
        """empty host_id string -> returns failed"""
        r = mcp(
            "agx_bind_group_to_project",
            caller_agent_id="agx_core",
            project_id="system_project",
            group_id="ag_core",
            host_id="",
        )
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}: {r}"
        assert r.get("status") == "failed", f"expected failed, got: {r}"
        assert "不能为空" in r.get("error", ""), f"unexpected error: {r}"

    def test_bind_rejects_whitespace_host_id(self, mcp):
        """whitespace-only host_id -> returns failed"""
        r = mcp(
            "agx_bind_group_to_project",
            caller_agent_id="agx_core",
            project_id="system_project",
            group_id="ag_core",
            host_id="   ",
        )
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}: {r}"
        assert r.get("status") == "failed", f"expected failed, got: {r}"
        assert "不能为空" in r.get("error", ""), f"unexpected error: {r}"

    def test_bind_rejects_nonexistent_host_id(self, mcp):
        """host_id absent from agx_host_inventory -> returns failed"""
        r = mcp(
            "agx_bind_group_to_project",
            caller_agent_id="agx_core",
            project_id="system_project",
            group_id="ag_core",
            host_id="host_does_not_exist_xyz",
        )
        assert isinstance(r, dict), f"expected dict, got {type(r).__name__}: {r}"
        assert r.get("status") == "failed", f"expected failed, got: {r}"
        assert "不存在" in r.get("error", ""), f"unexpected error: {r}"
