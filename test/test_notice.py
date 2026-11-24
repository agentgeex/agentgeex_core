"""
Notice system E2E tests
Covers: agx_write_notice (5 types), agx_get_notice, agx_query_notices (various filters)
"""

import re

import pytest

pytestmark = pytest.mark.notice

# the correct notice table name
NOTICE_TABLE = "agx_agent_notice"


def _extract_notice_id(text) -> str:
    if isinstance(text, dict):
        return text.get("notice_id", "")
    m = re.search(r"(note_[a-f0-9]+)", str(text))
    return m.group(1) if m else ""


class TestWriteNotice:
    """agx_write_notice — write notices (5 types)"""

    @pytest.mark.parametrize(
        "notice_type",
        [
            "log",
            "decision",
            "error",
            "handoff",
            "config",
        ],
    )
    def test_all_types(self, mcp, mysql, notice_type):
        content = f"pytest - {notice_type} test notice"
        r = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type=notice_type,
            content=content,
            title=f"Test {notice_type}",
        )
        nid = _extract_notice_id(r)
        assert nid, f"cannot extract notice_id: {r}"

        # verify
        row = mysql(f"SELECT notice_id, notice_type, title FROM {NOTICE_TABLE} WHERE notice_id='{nid}'")
        assert row, f"notice {nid} not in DB"
        assert row[0]["notice_type"] == notice_type

        # cleanup
        mysql(f"UPDATE {NOTICE_TABLE} SET active_status=0 WHERE notice_id='{nid}'")

    def test_invalid_type_rejected(self, mcp):
        r = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type="invalid_type",
            content="should fail",
        )
        msg = str(r)
        assert "error" in msg.lower() or "invalid" in msg.lower(), f"invalid type should be rejected: {r}"

    def test_private_visibility(self, mcp, mysql):
        r = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type="log",
            content="private notice",
            visibility="private",
        )
        nid = _extract_notice_id(r)
        assert nid

        row = mysql(f"SELECT visibility FROM {NOTICE_TABLE} WHERE notice_id='{nid}'")
        assert row and row[0]["visibility"] == "private"

        mysql(f"UPDATE {NOTICE_TABLE} SET active_status=0 WHERE notice_id='{nid}'")

    def test_network_visibility(self, mcp, mysql):
        r = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type="log",
            content="network notice",
            visibility="network",
        )
        nid = _extract_notice_id(r)
        assert nid

        row = mysql(f"SELECT visibility FROM {NOTICE_TABLE} WHERE notice_id='{nid}'")
        assert row and row[0]["visibility"] == "network"

        mysql(f"UPDATE {NOTICE_TABLE} SET active_status=0 WHERE notice_id='{nid}'")

    def test_with_task_and_run(self, mcp, mysql, core_agent_id):
        # dispatch a run first
        dr = mcp(
            "agx_dispatch_task", caller_agent_id="agx_core", instruction="E2E - notice test", agent_id=core_agent_id
        )
        assert isinstance(dr, dict) and "run_uuid" in dr
        ru = dr["run_uuid"]
        tid = dr.get("task_id", "")

        r = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type="log",
            content="notice with links",
            task_id=tid,
            run_uuid=ru,
        )
        nid = _extract_notice_id(r)
        assert nid

        row = mysql(f"SELECT task_id, run_uuid FROM {NOTICE_TABLE} WHERE notice_id='{nid}'")
        assert row

        mysql(f"UPDATE {NOTICE_TABLE} SET active_status=0 WHERE notice_id='{nid}'")
        mcp(
            "agx_finish_task",
            caller_agent_id="agx_core",
            task_run_uuid=ru,
            status="succeeded",
            summary="pytest cleanup",
        )


class TestGetNotice:
    def test_get_existing(self, mcp, mysql):
        # write one record first
        wr = mcp(
            "agx_write_notice",
            caller_agent_id="agx_core",
            agent_id="agx_core",
            project_id="system_project",
            notice_type="log",
            content="test get_notice",
            title="Get Notice Test",
        )
        nid = _extract_notice_id(wr)
        assert nid

        r = mcp("agx_get_notice", caller_agent_id="agx_core", notice_id=nid)
        data = r if isinstance(r, dict) else {}
        assert data.get("notice_id") == nid, f"get_notice failed: {data}"

        mysql(f"UPDATE {NOTICE_TABLE} SET active_status=0 WHERE notice_id='{nid}'")


class TestQueryNotices:
    """agx_query_notices — multi-dimensional query; returns a paged dict {total, notices[], limit, offset}"""

    def _get_notices(self, r):
        """Extract the notices list from a query result."""
        if isinstance(r, list):
            return r
        if isinstance(r, dict) and "notices" in r:
            return r["notices"]
        return []

    def test_query_all(self, mcp):
        r = mcp("agx_query_notices", caller_agent_id="agx_core")
        notices = self._get_notices(r)
        assert isinstance(notices, list), f"expected list: {r}"

    def test_filter_by_type(self, mcp):
        r = mcp("agx_query_notices", caller_agent_id="agx_core", notice_type="log")
        notices = self._get_notices(r)
        assert isinstance(notices, list)

    def test_filter_by_keyword(self, mcp):
        r = mcp("agx_query_notices", caller_agent_id="agx_core", keyword="pytest")
        notices = self._get_notices(r)
        assert isinstance(notices, list)

    def test_filter_by_visibility(self, mcp):
        r = mcp("agx_query_notices", caller_agent_id="agx_core", visibility="project")
        notices = self._get_notices(r)
        assert isinstance(notices, list)

    def test_pagination(self, mcp):
        r = mcp("agx_query_notices", caller_agent_id="agx_core", limit=5, offset=0)
        notices = self._get_notices(r)
        assert isinstance(notices, list) and len(notices) <= 5

    def test_active_status_default(self, mcp):
        """default active_status=1 returns only active notices"""
        r = mcp("agx_query_notices", caller_agent_id="agx_core")
        notices = self._get_notices(r)
        assert isinstance(notices, list)
        for item in notices:
            if isinstance(item, dict):
                assert item.get("active_status") in (None, "1", 1), f"archived notice in default query: {item}"

    def test_active_status_all(self, mcp):
        """active_status='' returns everything"""
        r = mcp("agx_query_notices", caller_agent_id="agx_core", active_status="")
        notices = self._get_notices(r)
        assert isinstance(notices, list)
