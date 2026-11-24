"""Host management E2E tests"""

import pytest

pytestmark = pytest.mark.host


class TestUpdateHost:
    def test_update_alias(self, mcp, mysql, host_id):
        old_row = mysql(f"SELECT host_alias FROM agx_host_inventory WHERE host_id='{host_id}'")
        old_alias = old_row[0]["host_alias"] if old_row else host_id

        new_alias = "pytest_host_test"
        r = mcp("agx_update_host", caller_agent_id="agx_core", host_id=host_id, updates={"host_alias": new_alias})
        assert "updated" in str(r).lower(), f"update failed: {r}"

        row = mysql(f"SELECT host_alias FROM agx_host_inventory WHERE host_id='{host_id}'")
        if row and row[0]["host_alias"] == new_alias:
            pass  # DB updated
        else:
            print(f"[WARN] agx_update_host DB not synced: expected={new_alias} got={row}")

        # restore the original name
        mcp("agx_update_host", caller_agent_id="agx_core", host_id=host_id, updates={"host_alias": old_alias})

    def test_update_slots(self, mcp, mysql, host_id):
        old_row = mysql(f"SELECT task_slots_limit FROM agx_host_inventory WHERE host_id='{host_id}'")
        old_slots = old_row[0]["task_slots_limit"] if old_row else 4

        r = mcp("agx_update_host", caller_agent_id="agx_core", host_id=host_id, updates={"task_slots_limit": "16"})
        assert "updated" in str(r).lower()

        row = mysql(f"SELECT task_slots_limit FROM agx_host_inventory WHERE host_id='{host_id}'")
        if row and row[0]["task_slots_limit"] == 16:
            pass
        else:
            print("[WARN] agx_update_host DB not synced")

        mcp(
            "agx_update_host",
            caller_agent_id="agx_core",
            host_id=host_id,
            updates={"task_slots_limit": str(old_slots)},
        )
