"""Tests for explicitly passing session_key (pin/reuse a session).

Goals:
1. agx_dispatch_task with an explicit session_key -> execution.session_id equals that key
2. Two dispatches with the same session_key -> land on the same session
   (agx_session.run_count +1, exactly one row)
3. No session_key -> the previous derivation applies (backward compatible)
4. agx_schedule_task behaves the same way

Everything runs against agentgeex_test plus Redis DB1 (conftest's double gate
guarantees isolation).
"""

import json
import sys
from pathlib import Path

import pytest

_AGX_DIR = Path(__file__).resolve().parent.parent / "agx_mcp"
if str(_AGX_DIR) not in sys.path:
    sys.path.insert(0, str(_AGX_DIR))

import agx_core  # noqa: E402


def _assert_not_mocked():
    """Guard: detects whether global dependencies were polluted by module-level
    mocks from other test modules.

    Background: test_agx_book_orchestrate_chapter.py replaces sys.modules['redis']
    with a MagicMock at import time and never restores it, polluting the whole
    pytest session. This test needs the real DB/Redis, so it skips (rather than
    fails) when pollution is detected, and suggests running it on its own.
    """
    import redis as _r

    from unittest.mock import MagicMock

    if isinstance(_r, MagicMock) or not hasattr(_r, "ConnectionPool"):
        pytest.skip("redis 被上游测试 mock 污染，请单独跑：pytest test/test_session_key.py")


def _q(sql, params=None, commit=False):
    return agx_core.execute_sql(sql, params, commit)


@pytest.fixture(scope="module")
def probe_agent():
    """Pick an already-deployed agent as the target."""
    _assert_not_mocked()
    rows = _q(
        "SELECT agent_id, host_id, project_id FROM agx_agent_registry "
        "WHERE deploy_status='deployed' AND active_status=1 LIMIT 1"
    )
    if not rows:
        pytest.skip("无已部署 agent 可用于测试")
    return rows[0]


@pytest.fixture(scope="module")
def general_task(probe_agent):
    rows = _q(
        "SELECT task_id FROM agx_task_base WHERE agent_id=%s AND task_labs='general' AND active_status=1 LIMIT 1",
        (probe_agent["agent_id"],),
    )
    if not rows:
        pytest.skip("目标 agent 无 general task")
    return rows[0]["task_id"]


def _cleanup(session_key):
    """Clean up the execution and session records created by this test."""
    _q("DELETE FROM agx_task_execution WHERE session_id=%s", (session_key,), commit=True)
    _q("DELETE FROM agx_session WHERE session_id=%s", (session_key,), commit=True)


def test_dispatch_explicit_session_key(probe_agent, general_task):
    """① explicit session_key -> execution.session_id equals that key exactly"""
    from mcp_server import agx_dispatch_task

    key = "agent:test_probe:explicit_kv_test"
    _cleanup(key)
    try:
        out = json.loads(
            agx_dispatch_task(
                caller_agent_id="agentgeex_agent",
                instruction="【测试】显式 session_key 验证",
                agent_id=probe_agent["agent_id"],
                task_id=general_task,
                session_key=key,
                needs_reply=0,
            )
        )
        assert "error" not in out, out
        run_uuid = out["run_uuid"]

        rows = _q("SELECT session_id FROM agx_task_execution WHERE task_run_uuid=%s", (run_uuid,))
        assert rows, "execution 未创建"
        assert rows[0]["session_id"] == key, f"session_id 应为 {key}, 实际 {rows[0]['session_id']}"

        srows = _q("SELECT session_name, run_count FROM agx_session WHERE session_id=%s", (key,))
        assert srows, "agx_session 未登记"
    finally:
        _cleanup(key)


def test_dispatch_same_key_twice_reuses_session(probe_agent, general_task):
    """★ Core case: two dispatches with the same session_key land on the same session row and run_count grows by exactly 2"""
    from mcp_server import agx_dispatch_task

    key = "agent:test_probe:reuse_kv_test"
    _cleanup(key)  # clean starting point
    try:
        outs = []
        for i in (1, 2):
            out = json.loads(
                agx_dispatch_task(
                    caller_agent_id="agentgeex_agent",
                    instruction=f"【测试】同一会话第 {i} 个任务",
                    agent_id=probe_agent["agent_id"],
                    task_id=general_task,
                    session_key=key,
                    session_name="测试·同一会话复用",
                    needs_reply=0,
                )
            )
            assert "error" not in out, out
            outs.append(out["run_uuid"])

        # both executions point at the same session_id
        execs = _q(
            "SELECT task_run_uuid, session_id FROM agx_task_execution WHERE task_run_uuid IN (%s,%s)",
            (outs[0], outs[1]),
        )
        assert len(execs) == 2, f"应有 2 条 execution, 实际 {len(execs)}"
        assert all(r["session_id"] == key for r in execs), f"session_id 不一致: {execs}"

        # ★ agx_session holds exactly one row (two tasks do not split into two sessions)
        sessions = _q("SELECT session_name, run_count FROM agx_session WHERE session_id=%s", (key,))
        assert len(sessions) == 1, f"agx_session 应只 1 行, 实际 {len(sessions)}"
        # clean starting point + two registrations -> run_count is exactly 2
        assert sessions[0]["run_count"] == 2, f"run_count 应=2, 实际 {sessions[0]['run_count']}"
    finally:
        _cleanup(key)


def test_schedule_explicit_session_key(probe_agent, general_task):
    """③ agx_schedule_task with an explicit session_key -> the pending record points at that session"""
    from mcp_server import agx_schedule_task

    key = "agent:test_probe:schedule_kv_test"
    _cleanup(key)
    try:
        out = json.loads(
            agx_schedule_task(
                caller_agent_id="agentgeex_agent",
                instruction="【测试】schedule 显式 session_key",
                agent_id=probe_agent["agent_id"],
                task_id=general_task,
                session_key=key,
                needs_reply=0,
            )
        )
        assert "error" not in out, out
        run_uuid = out["run_uuid"]
        rows = _q("SELECT session_id FROM agx_task_execution WHERE task_run_uuid=%s", (run_uuid,))
        assert rows and rows[0]["session_id"] == key, f"session_id 应为 {key}: {rows}"
    finally:
        _cleanup(key)


def test_no_session_key_backward_compatible(probe_agent, general_task):
    """④ no session_key -> falls back to the previous derivation (general -> default_session_id); behaviour unchanged"""
    from mcp_server import agx_dispatch_task

    out = json.loads(
        agx_dispatch_task(
            caller_agent_id="agentgeex_agent",
            instruction="【测试】不传 session_key 向后兼容",
            agent_id=probe_agent["agent_id"],
            task_id=general_task,
            needs_reply=0,
        )
    )
    assert "error" not in out, out
    run_uuid = out["run_uuid"]
    try:
        rows = _q(
            "SELECT e.session_id, a.default_session_id FROM agx_task_execution e "
            "JOIN agx_agent_registry a ON e.agent_id=a.agent_id WHERE e.task_run_uuid=%s",
            (run_uuid,),
        )
        assert rows, "execution 未创建"
        assert rows[0]["session_id"] == rows[0]["default_session_id"], (
            f"应回退 default_session_id: {rows[0]}"
        )
    finally:
        _q("DELETE FROM agx_task_execution WHERE task_run_uuid=%s", (run_uuid,))
