"""
agx_self_dispatch — the self-dispatch exit (design decision made 2026-10-01) E2E tests

Design notes:
    - Purpose: give roles without L5 (e.g. worker) a way to assign work to
      themselves (sending work to others = command = L5; sending work to
      yourself = self-management = L3)
    - Hard constraint: agent_id is hard-coded to the caller; no parameter can
      target another agent
    - Destination: the caller's own universal channel (dedicated session,
      concurrency-safe)

What this test checks:
    1. The tool is registered (it appears in the MCP tool list)
    2. The worker role can call it (L3 passes; not blocked by the level system)
    3. There is no "target another agent" parameter (privilege escalation is
       impossible at the signature level)

Run: cd agx && uv run pytest test/test_self_dispatch.py -v
"""

import pytest
from conftest import AID_CORE

pytestmark = pytest.mark.self_dispatch

# worker template agent in the test DB (deploy_status='template', not deployed)
WORKER_TEMPLATE = "py_architect"


class TestSelfDispatch:
    """Permissions and registration of agx_self_dispatch."""

    def test_tool_registered(self, mcp):
        """Tool is registered: the call does not return an 'unknown tool'-style error."""
        r = mcp("agx_self_dispatch", caller_agent_id=AID_CORE, instruction="self-check")
        # whatever the business outcome, it must never report that the tool does not exist
        assert not (isinstance(r, dict) and "unknown tool" in str(r).lower()), r

    def test_worker_permitted_l3(self, mcp, get):
        """A worker (L3) can call it: it is not rejected by the level system.

        The worker template agent is not deployed, so the business layer
        reports "not deployed" — which is itself evidence that the permission
        check passed (the call reached the business logic).
        (If the level system had blocked it, we would see a permission-denied
        or level-related error instead.)
        """
        r = get(mcp("agx_self_dispatch", caller_agent_id=WORKER_TEMPLATE, instruction="巡检队列"))
        assert r is not None
        text = str(r)
        assert "permission_denied" not in text
        assert "requires level" not in text.lower()
        assert "not permitted" not in text.lower()
        # expected: reaches the business layer and reports "not deployed" (template state)
        assert "未部署" in text or "deploy_status" in text or "error" in text

    def test_requires_instruction(self, mcp, get):
        """instruction is required."""
        r = get(mcp("agx_self_dispatch", caller_agent_id=AID_CORE, instruction=""))
        assert r is not None
        assert "error" in str(r).lower()

    def test_no_other_agent_param(self):
        """No "target another agent" parameter exists at the signature level (the strongest anti-escalation guarantee)."""
        import ast
        import os

        src_path = os.path.join(os.path.dirname(__file__), "..", "agx_mcp", "mcp_server.py")
        with open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "agx_self_dispatch":
                fn = node
                break
        assert fn is not None, "agx_self_dispatch not found"
        params = [a.arg for a in fn.args.args]
        # allowed parameter set (no agent_id / target-style parameters)
        assert "agent_id" not in params, f"unexpected agent_id param: {params}"
        assert "target_agent_id" not in params
        assert "recipient" not in params
        assert "caller_agent_id" in params
