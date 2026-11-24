"""
exec_name (short exec title) tests — unit + E2E

Covers:
  - derive_exec_name and its three-tier lookup (explicit / extracted from the
    instruction / fallback to the task description)
  - whether dispatch / schedule / continue all persist exec_name correctly
  - the watchdog path: internal _ao_dispatch_task with a JSON instruction
    (guards against falling back to first-line extraction)

★ Everything runs isolated: MySQL agentgeex_test + Redis DB 1 (see conftest.py).
  The config file test/agx_config.test.json injects AGX_CONFIG_PATH when this
  module is imported, so agx_core imported directly also points at the test
  database and never touches production.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from conftest import HOST_ID, NW

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("AGX_CONFIG_PATH", str(ROOT / "test" / "agx_config.test.json"))

TEST_DB = "agentgeex_test"
AID_CORE = "agx_core"

pytestmark = pytest.mark.exec_name


# ═══════════════════════════════════════════════════════════════
# unit tests: derive_exec_name (pure function, no DB / MCP)
# ═══════════════════════════════════════════════════════════════


class TestDeriveExecName:
    """Three-tier lookup: (1) explicit (2) extracted from the instruction (3) fallback to the task description"""

    @pytest.fixture(scope="class")
    def d(self):
        if str(ROOT / "mcp") not in sys.path:
            sys.path.insert(0, str(ROOT / "mcp"))
        from agx_core import derive_exec_name

        return derive_exec_name

    # ── (1) explicit value ──
    def test_explicit_wins(self, d):
        """An explicit value always wins and no extraction happens"""
        assert d("我指定的名字", "## 无关标题", "general", "描述") == "我指定的名字"

    def test_explicit_stripped(self, d):
        assert d("  带空格的名字  ", "", "general", "") == "带空格的名字"

    # ── (2) JSON form (watchdog alerts / receipts) ──
    def test_watchdog_alert_type(self, d):
        """A watchdog alert instruction is JSON -> must take the JSON branch and yield the localised type label"""
        instr = json.dumps({"alert_type": "watchdog", "target_run_uuid": "run_x", "reason": "timeout"})
        assert d("", instr, "watchdog_alert", "处理报警") == "看门狗报警"

    def test_reply_type(self, d):
        instr = json.dumps({"reply_type": "task_completion", "status": "succeeded", "summary": "OK"})
        assert d("", instr, "general", "回执") == "任务完成回执"

    def test_json_semantic_title_field(self, d):
        """When the JSON carries a title, the title wins"""
        instr = json.dumps({"title": "桥② CPC→UNSPSC", "alert_type": "watchdog"})
        assert d("", instr, "general", "描述") == "桥② CPC→UNSPSC"

    def test_json_broken_falls_back(self, d):
        """Malformed JSON -> fall back to the task description, never raise"""
        assert d("", "{not valid json", "general", "回退描述") == "回退描述"

    # ── (2) plain-text form ──
    def test_text_heading_preferred(self, d):
        """A plain-text instruction prefers the markdown heading line"""
        instr = "【项目背景】\n- 项目：赢立方\n\n## 章节标题\n正文"
        assert d("", instr, "universal", "通用") == "章节标题"

    def test_text_skips_boilerplate(self, d):
        """Skip non-distinctive template lines (project background and similar) and take the first real body line"""
        instr = "【项目背景】\n- 项目：赢立方"
        assert d("", instr, "universal", "通用万能") == "项目：赢立方"

    def test_text_plain(self, d):
        assert d("", "pytest - dispatched run", "general", "默认任务") == "pytest - dispatched run"

    # ── (3) fallback ──
    def test_fallback_to_task_desc(self, d):
        assert d("", "", "general", "仅描述") == "仅描述"

    def test_all_empty(self, d):
        assert d("", "", "", "") == ""

    def test_none_instruction(self, d):
        assert d("", None, "general", "none 指令") == "none 指令"

    def test_length_capped(self, d):
        """An over-long name is truncated below 255 characters"""
        assert len(d("x" * 400, "", "", "")) <= 255


# ═══════════════════════════════════════════════════════════════
# E2E: MCP persistence points (runs against agentgeex_test)
# ═══════════════════════════════════════════════════════════════


class TestDispatchExecName:
    """agx_dispatch_task persistence point"""

    def test_explicit_exec_name_persisted(self, mcp, mysql):
        r = mcp(
            "agx_dispatch_task",
            caller_agent_id=AID_CORE,
            instruction="E2E exec_name 显式",
            agent_id=AID_CORE,
            exec_name="E2E·显式标题",
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"dispatch failed: {r}"
        run_uuid = r["run_uuid"]
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        assert rows and rows[0]["exec_name"] == "E2E·显式标题", f"got {rows}"
        mcp("agx_finish_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid, status="succeeded", summary="test")

    def test_derived_from_instruction(self, mcp, mysql):
        r = mcp(
            "agx_dispatch_task",
            caller_agent_id=AID_CORE,
            instruction="## E2E 自动推导标题\n正文忽略",
            agent_id=AID_CORE,
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"dispatch failed: {r}"
        run_uuid = r["run_uuid"]
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        assert rows and rows[0]["exec_name"] == "E2E 自动推导标题", f"got {rows}"
        mcp("agx_finish_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid, status="succeeded", summary="test")


class TestScheduleExecName:
    """agx_schedule_task persistence point (pending)"""

    def test_schedule_persists_exec_name(self, mcp, mysql):
        r = mcp(
            "agx_schedule_task",
            caller_agent_id=AID_CORE,
            instruction="E2E schedule 标题",
            agent_id=AID_CORE,
            exec_name="E2E·计划步骤",
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"schedule failed: {r}"
        run_uuid = r["run_uuid"]
        rows = mysql(f"SELECT exec_name, run_status FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'")
        assert rows and rows[0]["exec_name"] == "E2E·计划步骤", f"got {rows}"
        assert rows[0]["run_status"] == "pending"
        mcp("agx_finish_task", caller_agent_id=AID_CORE, task_run_uuid=run_uuid, status="succeeded", summary="test")


class TestContinueExecName:
    """agx_continue_task persistence point (keeps the original name plus a continuation marker)"""

    def test_continue_inherits_with_suffix(self, mcp, mysql):
        r = mcp(
            "agx_dispatch_task",
            caller_agent_id=AID_CORE,
            instruction="E2E 原始执行",
            agent_id=AID_CORE,
            exec_name="E2E·原始名",
        )
        assert isinstance(r, dict) and "run_uuid" in r, f"dispatch failed: {r}"
        orig = r["run_uuid"]

        r2 = mcp(
            "agx_continue_task",
            caller_agent_id=AID_CORE,
            original_run_uuid=orig,
            instruction="继续追问",
        )
        assert isinstance(r2, dict) and "run_uuid" in r2, f"continue failed: {r2}"
        new = r2["run_uuid"]
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{new}'")
        assert rows and rows[0]["exec_name"] == "E2E·原始名（续接）", f"got {rows}"

        for ru in (orig, new):
            mcp("agx_finish_task", caller_agent_id=AID_CORE, task_run_uuid=ru, status="succeeded", summary="test")


# ═══════════════════════════════════════════════════════════════
# watchdog path: internal _ao_dispatch_task (bypasses MCP)
# ═══════════════════════════════════════════════════════════════


class TestWatchdogInternalPath:
    """The watchdog calls _ao_dispatch_task directly with a JSON instruction — the name must still be derived correctly"""

    @pytest.fixture(scope="class")
    def dispatch_internal(self):
        if str(ROOT / "mcp") not in sys.path:
            sys.path.insert(0, str(ROOT / "mcp"))
        from agx_core import _ao_dispatch_task

        created = []

        def _call(instruction, exec_name=""):
            res = _ao_dispatch_task(
                caller_agent_id="agx_core",
                instruction=instruction,
                agent_id="agx_core",
                task_id="",
                network_id=NW,
                project_id="system_project",
                host_id=HOST_ID,
                sender_id="agx_core",
                timeout_seconds=60,
                exec_name=exec_name,
            )
            if "run_uuid" in res:
                created.append(res["run_uuid"])
            return res

        yield _call

    def test_watchdog_json_without_name_derives_type(self, dispatch_internal, mysql):
        """Watchdog alert: no exec_name passed, JSON instruction -> derived as the watchdog alert label"""
        instr = json.dumps({"alert_type": "watchdog", "target_run_uuid": "run_abc", "reason": "timeout"})
        res = dispatch_internal(instr)
        assert "run_uuid" in res, f"failed: {res}"
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{res['run_uuid']}'")
        assert rows and rows[0]["exec_name"] == "看门狗报警", f"got {rows}"

    def test_watchdog_explicit_name_persisted(self, dispatch_internal, mysql):
        """Watchdog alert: an explicit name is passed (the real code path)"""
        instr = json.dumps({"alert_type": "watchdog", "target_run_uuid": "run_abc", "reason": "timeout"})
        res = dispatch_internal(instr, exec_name="看门狗报警·timeout·run_abc")
        assert "run_uuid" in res, f"failed: {res}"
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{res['run_uuid']}'")
        assert rows and rows[0]["exec_name"] == "看门狗报警·timeout·run_abc", f"got {rows}"

    def test_reply_json_derives_type(self, dispatch_internal, mysql):
        """Receipt: a JSON reply_type -> derived as the task-completion receipt label"""
        instr = json.dumps({"reply_type": "task_completion", "original_run_uuid": "run_x", "status": "succeeded"})
        res = dispatch_internal(instr)
        assert "run_uuid" in res, f"failed: {res}"
        rows = mysql(f"SELECT exec_name FROM agx_task_execution WHERE task_run_uuid='{res['run_uuid']}'")
        assert rows and rows[0]["exec_name"] == "任务完成回执", f"got {rows}"


# ═══════════════════════════════════════════════════════════════
# session-name coupling: exec_name fixes the duplicate-name problem in agx_session
# ═══════════════════════════════════════════════════════════════


class TestSessionNameHint:
    """_session_name_hint gained an exec_name tier"""

    @pytest.fixture(scope="class")
    def hint(self):
        if str(ROOT / "mcp") not in sys.path:
            sys.path.insert(0, str(ROOT / "mcp"))
        from agx_core import _session_name_hint

        return _session_name_hint

    def test_exec_name_beats_task_desc(self, hint):
        """For a business task_labs, exec_name outranks the task description"""
        assert hint("", "book_revise_section", "模板描述", "章节A·修订") == "章节A·修订"

    def test_explicit_session_name_beats_exec_name(self, hint):
        assert hint("显式会话名", "book_revise_section", "模板描述", "章节A·修订") == "显式会话名"

    def test_general_labs_ignored(self, hint):
        """Generic task_labs such as general/universal are still left to automatic derivation"""
        assert hint("", "general", "通用描述", "某次执行") == ""
        assert hint("", "universal", "通用描述", "某次执行") == ""

    def test_fallback_to_task_desc(self, hint):
        assert hint("", "book_revise_section", "模板描述", "") == "模板描述"
