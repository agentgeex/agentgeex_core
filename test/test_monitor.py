"""
Runtime monitoring E2E tests
Covers: agx_running_tasks, agx_get_agent_queue_length, agx_list_all_agent_queues
"""

import json

import pytest

pytestmark = pytest.mark.monitor


class TestRunningTasks:
    def test_returns_list(self, mcp):
        r = mcp("agx_running_tasks", caller_agent_id="agx_core")
        assert r is not None
        # agx_running_tasks returns a list; mcporter prints the JSON array directly
        data = json.loads(r) if isinstance(r, str) else r
        assert data is not None, f"no result: {r}"

    def test_filter_by_agent(self, mcp):
        r = mcp("agx_running_tasks", caller_agent_id="agx_core", agent_id="agx_core")
        assert r is not None


class TestQueueLength:
    def test_core_agent_queue(self, mcp):
        r = mcp("agx_get_agent_queue_length", caller_agent_id="agx_core", agent_id="agx_core")
        # returns a JSON string or a dict
        msg = str(r)
        assert "agx_core" in msg or isinstance(r, dict), f"unexpected: {r}"


class TestAllQueues:
    def test_list_queues(self, mcp):
        r = mcp("agx_list_all_agent_queues", caller_agent_id="agx_core")
        assert r is not None
