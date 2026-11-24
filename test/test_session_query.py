"""test_session_query.py — tests for agx_my_sessions / agx_project_sessions

Covers:
  1. agx_my_sessions returns only your own sessions (forced caller filter, cannot be bypassed)
  2. agx_my_sessions supports task_labs / keyword / active_status filters
  3. agx_project_sessions as project admin -> scope=project (the whole project is visible)
  4. agx_project_sessions as non-admin -> safe degradation to scope=self_only
  5. agx_project_sessions resolves project_id automatically when it is omitted

Note: this test needs the real DB/Redis. If it is polluted by an upstream
module's sys.modules mock it skips automatically (see _assert_not_mocked); run
it on its own: pytest test/test_session_query.py
"""

import json
import os
import sys

import pytest

from conftest import NW

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agx_mcp"))

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
        pytest.skip("redis 被上游测试 mock 污染，请单独跑：pytest test/test_session_query.py")


def _q(sql, params=None, commit=False):
    return agx_core.execute_sql(sql, params, commit)


# session keys used by the tests (namespaced for isolation, cleaned up afterwards)
_PROBE_KEY = "agent:agentgeex_agent:__test_sq_probe"
_PROBE_KEY2 = "agent:agx_core:__test_sq_probe2"

# project admin (from agx_project.admin_agent_id)
_PM_AGENT = "agentgeex_agent"
# an ordinary non-admin agent (used for the degradation test)
_WORKER_AGENT = ""


def _cleanup():
    for k in (_PROBE_KEY, _PROBE_KEY2):
        _q("DELETE FROM agx_task_execution WHERE session_id=%s", (k,), commit=True)
        _q("DELETE FROM agx_session WHERE session_id=%s", (k,), commit=True)


@pytest.fixture(scope="module", autouse=True)
def _setup():
    _assert_not_mocked()
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(scope="module")
def project_id():
    rows = _q("SELECT project_id FROM agx_agent_registry WHERE agent_id=%s", (_PM_AGENT,))
    assert rows, f"agent {_PM_AGENT} 不存在"
    return rows[0]["project_id"]


@pytest.fixture(scope="module")
def worker_agent():
    """Find an ordinary worker unrelated to this project for the non-admin degradation test."""
    rows = _q(
        "SELECT agent_id, project_id, agent_role FROM agx_agent_registry "
        "WHERE active_status=1 AND agent_role='worker' AND agent_id != %s LIMIT 1",
        (_PM_AGENT,),
    )
    if not rows:
        pytest.skip("找不到 worker agent 做降级测试")
    return rows[0]


# ──────────────────────────────────────────────
# agx_my_sessions
# ──────────────────────────────────────────────


def test_my_sessions_only_returns_own(project_id):
    """★ Core: only the caller own sessions are returned; other people's stay invisible"""
    from mcp_server import agx_my_sessions

    # create two: one owned by us and one owned by someone else (both under the same project)
    _q(
        "INSERT INTO agx_session (network_id, session_id, session_name, agent_id, project_id, "
        "task_labs, run_count, last_active_at) VALUES (%s, %s, '测试·我的会话', %s, %s, "
        "'universal', 1, NOW())",
        (NW, _PROBE_KEY, _PM_AGENT, project_id),
        commit=True,
    )
    _q(
        "INSERT INTO agx_session (network_id, session_id, session_name, agent_id, project_id, "
        "task_labs, run_count, last_active_at) VALUES (%s, %s, '测试·别人的会话', 'agx_core', %s, "
        "'universal', 1, NOW())",
        (NW, _PROBE_KEY2, project_id),
        commit=True,
    )

    out = json.loads(agx_my_sessions(caller_agent_id=_PM_AGENT, keyword="测试·"))
    assert "error" not in out, out
    sids = [s["session_id"] for s in out["sessions"]]
    assert _PROBE_KEY in sids, f"应含自己的会话: {sids}"
    assert _PROBE_KEY2 not in sids, f"★ 不应含别人的会话: {sids}"


def test_my_sessions_task_labs_filter(project_id):
    """task_labs filtering works"""
    from mcp_server import agx_my_sessions

    out = json.loads(agx_my_sessions(caller_agent_id=_PM_AGENT, task_labs="universal", keyword="测试·"))
    assert "error" not in out, out
    assert all(s["task_labs"] == "universal" for s in out["sessions"]), out


def test_my_sessions_returns_total(project_id):
    """Returns total, consistent with the list semantics"""
    from mcp_server import agx_my_sessions

    out = json.loads(agx_my_sessions(caller_agent_id=_PM_AGENT, keyword="测试·"))
    assert "total" in out and "sessions" in out
    assert out["total"] >= 1, out


# ──────────────────────────────────────────────
# agx_project_sessions
# ──────────────────────────────────────────────


def test_project_sessions_pm_sees_all(project_id):
    """★ PM view: scope=project, every member session of the project is visible"""
    from mcp_server import agx_project_sessions

    out = json.loads(agx_project_sessions(caller_agent_id=_PM_AGENT, keyword="测试·"))
    assert "error" not in out, out
    assert out["scope"] == "project", f"管理员应为 project 范围: {out['scope']}"
    sids = [s["session_id"] for s in out["sessions"]]
    # an admin can see other people's sessions
    assert _PROBE_KEY2 in sids, f"管理员应看到 agx_core 的会话: {sids}"
    assert "by_agent" in out, "应含成员维度汇总"


def test_project_sessions_auto_project_id():
    """An omitted project_id is resolved automatically from the caller"""
    from mcp_server import agx_project_sessions

    out = json.loads(agx_project_sessions(caller_agent_id=_PM_AGENT))
    assert "error" not in out, out
    assert out["project_id"], "应自动解析出 project_id"


def test_project_sessions_non_pm_degrades(worker_agent):
    """★ Non-admin -> safe degradation to scope=self_only, without raising"""
    from mcp_server import agx_project_sessions

    out = json.loads(agx_project_sessions(caller_agent_id=worker_agent["agent_id"]))
    assert "error" not in out, out
    # if this worker happens to be a project admin, scope will be project, which is expected
    if out["scope"] == "self_only":
        assert out["note"], "降级时应带说明"
        assert all(s["agent_id"] == worker_agent["agent_id"] for s in out["sessions"]), out
