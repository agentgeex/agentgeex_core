"""
agx_book_orchestrate_chapter MCP unit tests
Covers: parameter validation, stage dispatch, plan writing/dedup, error handling

No real MySQL/Redis required — executeSql, _sync_entity,
_validate_and_expand_plan_steps and _deploy_executions are mocked.
"""

import importlib
import json
import os
import sys
import tempfile
from unittest import mock

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================
# dynamically load agx_book_orchestrate_chapter, bypassing the MCP framework
# ============================================================

def _load_ao_book_orchestrate_chapter():
    """Load it dynamically with importlib, mocking the top-level dependencies of mcp_server.py."""
    # mcp_server.py top-level import chain (in import order):
    #   config_loader, log_setup, fastmcp, permissions,
    #   redis (redis-py), common.mysql.mysqlconn, common.mysql.mysqlservice
    # mock all of them and keep only the pure Python standard library
    _modules_to_mock = [
        "fastmcp",
        "permissions",
        "redis",
    ]
    _saved = {}
    for _m in _modules_to_mock:
        if _m in sys.modules:
            _saved[_m] = sys.modules[_m]
        sys.modules[_m] = mock.MagicMock()

    # give the fastmcp mock a FastMCP attribute (a mock instance with a tool method)
    # tool() must return a decorator that hands back the decorated function unchanged
    _mcp_instance = mock.MagicMock()
    _mcp_instance.tool = lambda *a, **kw: (lambda fn: fn)  # @mcp.tool(...) -> return fn unchanged
    sys.modules["fastmcp"].FastMCP = lambda *a, **kw: _mcp_instance

    # give the redis mock ConnectionPool and Redis
    # give the permissions mock require_level and set_lookup_fn (both must return the decorated function unchanged)
    sys.modules["permissions"].require_level = lambda *a, **kw: (lambda fn: fn)
    sys.modules["permissions"].set_lookup_fn = lambda *a, **kw: None
    sys.modules["permissions"].ROLE_ALLOWED_LEVELS = {}

    # give the redis mock ConnectionPool and Redis
    sys.modules["redis"].ConnectionPool = mock.MagicMock
    sys.modules["redis"].Redis = mock.MagicMock

    try:
        from agx_mcp.mcp_server import agx_book_orchestrate_chapter
        return agx_book_orchestrate_chapter
    finally:
        pass  # do not restore the mocks; keep them for the whole test session


_ao_book_orchestrate_chapter = _load_ao_book_orchestrate_chapter()


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def book_root(tmp_path):
    """Create a temporary manuscript directory layout."""
    root = tmp_path / "book"
    root.mkdir()
    # create the chapter file
    chapter_file = root / "ch07_第7章 测试章节.md"
    chapter_file.write_text("# 第7章 测试\n\n## 7.1 第一节\n内容\n\n## 7.2 第二节\n内容\n")
    return str(root)


@pytest.fixture
def chapter_path(book_root):
    return os.path.join(book_root, "ch07_第7章 测试章节.md")


@pytest.fixture
def project_id():
    return "proj_test_12345"


@pytest.fixture
def caller_agent_id():
    return "agt_test_caller"


# ============================================================
# helpers
# ============================================================

def _make_plan_json(plan_id="plan_test_abc123", chapter="ch07", stage="write"):
    """Build a minimal valid plan JSON."""
    return {
        "plan_id": plan_id,
        "project_id": "proj_test_12345",
        "description": f"{chapter} 写作计划",
        "activate_mode": "chain",
        "start_task_run_uuid": "run_quality_write_ch07_abc123",
        "chapter": chapter,
        "split_dir": f"/tmp/book/workspace/split/{chapter}",
        "drafts_dir": f"/tmp/book/workspace/drafts/{chapter}",
        "stage": stage,
        "steps": [
            {
                "run_uuid": "run_w_ch07_s01_abc123",
                "task_id": "task_writer",
                "agent_id": "agt_writer",
                "instruction": "写 s01",
                "parent_run_uuid": "run_quality_write_ch07_abc123",
                "needs_reply": 1,
                "timeout_seconds": 3600,
            },
            {
                "run_uuid": "run_quality_write_ch07_abc123",
                "task_id": "task_quality",
                "agent_id": "agt_quality",
                "description": "quality gate",
                "instruction": "汇总写作结果",
                "children": ["run_w_ch07_s01_abc123"],
                "parent_run_uuid": "",
                "prev_task": "",
                "next_task": "",
            },
        ],
    }


def _mock_orch_run_success(plan_json, chapter="ch07", git_branch=""):
    """Simulate a successful ChapterOrchestrator.run() return."""
    plan_path = f"/tmp/book/workspace/plans/{chapter}/plan_write_abc123.json"
    return {
        "success": True,
        "stage": "write",
        "plan_id": plan_json["plan_id"],
        "chapter": chapter,
        "git_branch": git_branch,
        "split_dir": f"/tmp/book/workspace/split/{chapter}",
        "drafts_dir": f"/tmp/book/workspace/drafts/{chapter}",
        "plan_json_path": plan_path,
        "steps_count": len(plan_json["steps"]),
    }


def _mock_steps_validation(plan_json):
    """Simulate a successful _validate_and_expand_plan_steps return."""
    steps = plan_json["steps"]
    step_map = {s.get("run_uuid", ""): s for s in steps}
    entry_tasks = [
        s["run_uuid"]
        for s in steps
        if not s.get("parent_run_uuid") and "run_uuid" in s
    ]
    return "", step_map, steps, entry_tasks


# ============================================================
# test: stage=split (no plan JSON is generated, no database access)
# ============================================================

class TestStageSplit:
    """stage=split only splits; nothing is written to the database."""

    def test_split_no_db(self, book_root, chapter_path, project_id, caller_agent_id):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        # the split result carries no plan_json_path
        split_result = {
            "success": True,
            "stage": "split",
            "chapter": "ch07",
            "git_branch": "",
            "split_dir": f"{book_root}/workspace/split/ch07",
            "drafts_dir": f"{book_root}/workspace/drafts/ch07",
            "sections_count": 2,
        }

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = split_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = split_result["split_dir"]
            mock_inst.drafts_dir = split_result["drafts_dir"]

            result = json.loads(
                agx_book_orchestrate_chapter(
                    caller_agent_id=caller_agent_id,
                    book_root=book_root,
                    chapter_path=chapter_path,
                    project_id=project_id,
                    stage="split",
                )
            )

        assert result["success"] is False
        assert "plan_json_path" not in split_result
        # split fails because run() reports success while plan_json_path is empty
        # -> takes the "plan JSON file missing" branch
        assert "plan JSON 文件缺失" in result.get("errors", [""])[0]


# ============================================================
# test: stage=write (normal flow: orch -> plan -> DB -> deploy)
# ============================================================

class TestStageWrite:
    """The full stage=write flow."""

    def test_write_success(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)
        plan_path = run_result["plan_json_path"]
        step_map, validated_steps, entry_tasks = (
            {},  # not used after _mock_steps_validation
            plan_json["steps"],
            ["run_quality_write_ch07_abc123"],
        )

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            # mock the file as existing
            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        # first existence check -> nothing found
                        mock_sql.side_effect = [
                            [],  # SELECT 1 from agx_plan → empty
                            None,  # INSERT agx_plan (commit=True)
                        ]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )

                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 2}
                                    )

                                    result = json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="write",
                                        )
                                    )

        assert result["success"] is True
        assert result["plan_id"] == "plan_test_abc123"
        assert result["plan_status"] == "draft"
        assert result["steps_count"] == 2
        assert result["executions_deployed"] == 2
        assert "agx_activate_plan" in result.get("hint", "")

        # verify the ChapterOrchestrator arguments
        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            round_num=1,
            use_git=False,
        )

    def test_write_duplicate_plan_id(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """plan_id already exists -> returns an error."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        # existence check -> already present
                        mock_sql.return_value = [{"1": 1}]

                        result = json.loads(
                            agx_book_orchestrate_chapter(
                                caller_agent_id=caller_agent_id,
                                book_root=book_root,
                                chapter_path=chapter_path,
                                project_id=project_id,
                                stage="write",
                            )
                        )

        assert "error" in result
        assert "already exists" in result["error"]
        assert "plan_test_abc123" in result["error"]

    def test_write_missing_plan_file(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """plan JSON file missing -> returns an error."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        run_result = _mock_orch_run_success(_make_plan_json())
        # plan_json_path points at a file that does not exist
        run_result["plan_json_path"] = "/nonexistent/plan.json"

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            # isfile → False
            with mock.patch("os.path.isfile", return_value=False):
                result = json.loads(
                    agx_book_orchestrate_chapter(
                        caller_agent_id=caller_agent_id,
                        book_root=book_root,
                        chapter_path=chapter_path,
                        project_id=project_id,
                        stage="write",
                    )
                )

        assert result["success"] is False
        assert "plan JSON 文件缺失" in result["errors"][0]


# ============================================================
# test: stage=review (with round_num)
# ============================================================

class TestStageReview:
    """stage=review verifies that round_num is passed through."""

    def test_review_round_3(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json(stage="review")
        run_result = _mock_orch_run_success(plan_json)
        run_result["plan_json_path"] = run_result[
            "plan_json_path"
        ].replace("write", "review_r3")
        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 3}
                                    )

                                    json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="review",
                                            round_num=3,
                                        )
                                    )

        # the most important check: round_num=3 is passed through correctly
        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="review",
            round_num=3,
            use_git=False,
        )


# ============================================================
# test: stage=merge
# ============================================================

class TestStageMerge:
    """stage=merge"""

    def test_merge_success(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json(stage="merge")
        plan_json["description"] = "ch07 合并计划"
        run_result = _mock_orch_run_success(plan_json)
        run_result["plan_json_path"] = run_result[
            "plan_json_path"
        ].replace("write", "merge")

        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 2}
                                    )

                                    result = json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="merge",
                                        )
                                    )

        assert result["success"] is True
        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="merge",
            round_num=1,
            use_git=False,
        )


# ============================================================
# test: stage=full
# ============================================================

class TestStageFull:
    """stage=full"""

    def test_full_success(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json(stage="full")
        plan_json["description"] = "ch07 完整管线"
        run_result = _mock_orch_run_success(plan_json)
        run_result["plan_json_path"] = run_result[
            "plan_json_path"
        ].replace("write", "full")

        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 15}
                                    )

                                    result = json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="full",
                                        )
                                    )

        assert result["success"] is True
        assert result["executions_deployed"] == 15
        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="full",
            round_num=1,
            use_git=False,
        )


# ============================================================
# test: error handling
# ============================================================

class TestErrorHandling:
    """Exceptions and edge cases."""

    def test_orch_run_failure(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """ChapterOrchestrator.run() returns success=False."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        run_result = {
            "success": False,
            "errors": ["[validate] 章源文件不存在"],
        }

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result

            result = json.loads(
                agx_book_orchestrate_chapter(
                    caller_agent_id=caller_agent_id,
                    book_root=book_root,
                    chapter_path="/nonexistent/ch01.md",
                    project_id=project_id,
                    stage="write",
                )
            )

        assert result["success"] is False
        assert "章源文件不存在" in result["errors"][0]

    def test_orch_exception(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """ChapterOrchestrator.run() raises an exception."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            MockOrch.side_effect = RuntimeError("模拟运行时错误")

            result = json.loads(
                agx_book_orchestrate_chapter(
                    caller_agent_id=caller_agent_id,
                    book_root=book_root,
                    chapter_path=chapter_path,
                    project_id=project_id,
                    stage="write",
                )
            )

        assert result["success"] is False
        assert "RuntimeError" in result["errors"][0]
        assert "模拟运行时错误" in result["errors"][0]
        assert "traceback" in result

    def test_steps_validation_failure(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """_validate_and_expand_plan_steps returns an error."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.return_value = []  # the existence check returns nothing
                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                json.dumps(
                                    {
                                        "success": False,
                                        "errors": [
                                            "orphan step: run_xxx has no valid parent"
                                        ],
                                    }
                                ),
                                None,
                                None,
                                None,
                            )

                            result = json.loads(
                                agx_book_orchestrate_chapter(
                                    caller_agent_id=caller_agent_id,
                                    book_root=book_root,
                                    chapter_path=chapter_path,
                                    project_id=project_id,
                                    stage="write",
                                )
                            )

        assert result["success"] is False
        assert "orphan step" in result.get("errors", [""])[0]

    def test_deploy_failure(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """_deploy_executions reports a failure."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)
        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": False, "error": "no slots"}
                                    )

                                    result = json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="write",
                                        )
                                    )

        assert result.get("ok") is False
        assert "no slots" in result.get("error", "")


# ============================================================
# test: default values
# ============================================================

class TestDefaults:
    """Default-argument behaviour."""

    def test_default_stage_is_write(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """stage defaults to write when omitted."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)
        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 2}
                                    )

                                    json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                        )
                                    )

        # no stage passed -> defaults to "write"
        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            round_num=1,
            use_git=False,
        )

    def test_default_round_num_is_1(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        """round_num omitted -> defaults to 1."""
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)
        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = ""
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 2}
                                    )

                                    json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="write",
                                        )
                                    )

        MockOrch.assert_called_once_with(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            round_num=1,
            use_git=False,
        )


# ============================================================
# test: return structure
# ============================================================

class TestReturnStructure:
    """Verify the fields of a successful return."""

    def test_success_return_keys(
        self, book_root, chapter_path, project_id, caller_agent_id
    ):
        agx_book_orchestrate_chapter = _ao_book_orchestrate_chapter

        plan_json = _make_plan_json()
        run_result = _mock_orch_run_success(plan_json)
        validated_steps = plan_json["steps"]
        step_map = {}
        entry_tasks = ["run_quality_write_ch07_abc123"]

        with mock.patch(
            "tools.book_orchestrate.ChapterOrchestrator"
        ) as MockOrch:
            mock_inst = MockOrch.return_value
            mock_inst.run.return_value = run_result
            mock_inst.chapter = "ch07"
            mock_inst.branch_name = "feat/ch07_write"
            mock_inst.split_dir = run_result["split_dir"]
            mock_inst.drafts_dir = run_result["drafts_dir"]

            m_open = mock.mock_open(read_data=json.dumps(plan_json))

            with mock.patch("builtins.open", m_open):
                with mock.patch("os.path.isfile", return_value=True):
                    with mock.patch(
                        "agx_mcp.mcp_server.executeSql"
                    ) as mock_sql:
                        mock_sql.side_effect = [[], None]

                        with mock.patch(
                            "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                        ) as mock_validate:
                            mock_validate.return_value = (
                                "",
                                step_map,
                                validated_steps,
                                entry_tasks,
                            )
                            with mock.patch(
                                "agx_mcp.mcp_server._sync_entity"
                            ):
                                with mock.patch(
                                    "agx_mcp.mcp_server._deploy_executions"
                                ) as mock_deploy:
                                    mock_deploy.return_value = json.dumps(
                                        {"ok": True, "steps_deployed": 2}
                                    )

                                    result = json.loads(
                                        agx_book_orchestrate_chapter(
                                            caller_agent_id=caller_agent_id,
                                            book_root=book_root,
                                            chapter_path=chapter_path,
                                            project_id=project_id,
                                            stage="write",
                                        )
                                    )

        required_keys = [
            "success",
            "plan_id",
            "chapter",
            "git_branch",
            "split_dir",
            "drafts_dir",
            "plan_status",
            "steps_count",
            "executions_deployed",
            "entry_tasks",
            "hint",
        ]
        for k in required_keys:
            assert k in result, f"缺少字段: {k}"


# ============================================================
# agx_book_orchestrate_multi_chapter tests
# ============================================================


# ── aux ──
def _make_chapters_json(chapters_list):
    """Helper: build the chapters JSON string."""
    return json.dumps(chapters_list, ensure_ascii=False)


def _mock_sub_orch_run_success(plan_id="plan_ch03_test123"):
    """Simulate a successful agx_book_orchestrate_chapter return."""
    return json.dumps(
        {
            "success": True,
            "plan_id": plan_id,
            "chapter": "ch03",
            "git_branch": "",
            "split_dir": "/tmp/ws/ch03_test/split",
            "drafts_dir": "/tmp/ws/ch03_test/drafts",
            "plan_status": "draft",
            "steps_count": 4,
            "executions_deployed": 4,
            "entry_tasks": ["run_quality_write_ch03_abc"],
            "hint": "调用 agx_activate_plan 激活。",
        },
        ensure_ascii=False,
    )


def _mock_master_plan_json():
    """Simulate the MultiChapterOrchestrator.build_master_plan() return."""
    return {
        "plan_id": "plan_multi_2chapters",
        "project_id": "proj_test",
        "description": "多章编排（2 章，串行）",
        "activate_mode": "watchdog",
        "parallel": False,
        "steps": [
            {
                "run_uuid": "run_plan_execute_ch03",
                "agent_id": "agt_pm",
                "task_id": "task_plan_execute",
                "task_labs": "plan_execute",
                "task_type": "plan",
                "step_name": "编排 ch03",
                "description": "激活子计划 plan_ch03_test123",
                "instruction": "激活子计划并等待完成。\nplan_id: plan_ch03_test123\n章节: ch03",
                "children": [],
                "parent_run_uuid": "",
                "prev_task": "",
                "next_task": "run_plan_execute_ch04",
                "timeout_seconds": 7200,
                "needs_reply": 1,
                "x_desc": json.dumps({"plan_id": "plan_ch03_test123", "chapter": "ch03"}),
            },
            {
                "run_uuid": "run_plan_execute_ch04",
                "agent_id": "agt_pm",
                "task_id": "task_plan_execute",
                "task_labs": "plan_execute",
                "task_type": "plan",
                "step_name": "编排 ch04",
                "description": "激活子计划 plan_ch04_test456",
                "instruction": "激活子计划并等待完成。\nplan_id: plan_ch04_test456\n章节: ch04",
                "children": [],
                "parent_run_uuid": "",
                "prev_task": "run_plan_execute_ch03",
                "next_task": "",
                "timeout_seconds": 7200,
                "needs_reply": 1,
                "x_desc": json.dumps({"plan_id": "plan_ch04_test456", "chapter": "ch04"}),
            },
        ],
        "chapter_list": [
            {"chapter_path": "/book/ch03.md", "chapter_name": "ch03",
             "sub_plan_id": "plan_ch03_test123", "project_id": "proj_test",
             "stage": "full", "round_num": 1},
            {"chapter_path": "/book/ch04.md", "chapter_name": "ch04",
             "sub_plan_id": "plan_ch04_test456", "project_id": "proj_test",
             "stage": "full", "round_num": 1},
        ],
    }


class TestMultiChapterMCP:
    """MCP-layer tests for agx_book_orchestrate_multi_chapter."""

    def test_success_serial_two_chapters(self):
        """Two chapters in serial; agx_book_orchestrate_chapter succeeds for both."""
        from tools.book_orchestrate import MultiChapterOrchestrator

        chapters_list = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test"},
        ]
        master_plan = _mock_master_plan_json()
        validated_steps = master_plan["steps"]
        step_map = {
            s["run_uuid"]: {"run_uuid": s["run_uuid"], "agent_id": "agt_pm",
                              "task_id": "task_plan_execute", "task_labs": "plan_execute",
                              "task_type": "plan",
                              "description": s["description"],
                              "instruction": s["instruction"],
                              "children": [], "parent_run_uuid": "",
                              "prev_task": s.get("prev_task", ""),
                              "next_task": s.get("next_task", ""),
                              "timeout_seconds": 7200, "needs_reply": 1,
                              "x_desc": s.get("x_desc", "")}
            for s in validated_steps
        }
        entry_tasks = [s["run_uuid"] for s in validated_steps if not s.get("parent_run_uuid")]

        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()

        with mock.patch(
            "agx_mcp.mcp_server.agx_book_orchestrate_chapter"
        ) as mock_sub:
            mock_sub.side_effect = [
                _mock_sub_orch_run_success("plan_ch03_test123"),
                _mock_sub_orch_run_success("plan_ch04_test456"),
            ]

            with mock.patch(
                "tools.book_orchestrate.MultiChapterOrchestrator"
            ) as MockMC:
                mock_mc = MockMC.return_value
                mock_mc.build_master_plan.return_value = master_plan

                with mock.patch(
                    "agx_mcp.mcp_server.executeSql"
                ) as mock_sql:
                    mock_sql.return_value = []  # the existence check returns nothing

                    with mock.patch(
                        "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                    ) as mock_validate:
                        mock_validate.return_value = (
                            "", step_map, validated_steps, entry_tasks
                        )

                        with mock.patch(
                            "agx_mcp.mcp_server._sync_entity"
                        ):
                            with mock.patch(
                                "agx_mcp.mcp_server._deploy_executions"
                            ) as mock_deploy:
                                mock_deploy.return_value = json.dumps(
                                    {"ok": True, "steps_deployed": 2}
                                )

                                result = json.loads(
                                    agx_book_orchestrate_multi_chapter(
                                        caller_agent_id="agt_test",
                                        book_root="/tmp/book",
                                        chapters=_make_chapters_json(chapters_list),
                                        parallel=False,
                                    )
                                )

        assert result["success"] is True
        assert result["master_plan_id"] == "plan_multi_2chapters"
        assert len(result["sub_plan_ids"]) == 2
        assert result["sub_plan_ids"] == ["plan_ch03_test123", "plan_ch04_test456"]
        assert result["steps_count"] == 2
        assert result["chapters_count"] == 2
        assert result["parallel"] is False

    def test_success_parallel(self):
        """Parallel mode: success."""
        chapters_list = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test"},
        ]
        master_plan = _mock_master_plan_json()
        master_plan["parallel"] = True
        # parallel, no dependencies
        for s in master_plan["steps"]:
            s["prev_task"] = ""
            s["next_task"] = ""
        validated_steps = master_plan["steps"]
        step_map = {
            s["run_uuid"]: s for s in validated_steps
        }
        entry_tasks = [s["run_uuid"] for s in validated_steps]

        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()

        with mock.patch(
            "agx_mcp.mcp_server.agx_book_orchestrate_chapter"
        ) as mock_sub:
            mock_sub.side_effect = [
                _mock_sub_orch_run_success("plan_ch03_test123"),
                _mock_sub_orch_run_success("plan_ch04_test456"),
            ]

            with mock.patch(
                "tools.book_orchestrate.MultiChapterOrchestrator"
            ) as MockMC:
                mock_mc = MockMC.return_value
                mock_mc.build_master_plan.return_value = master_plan

                with mock.patch(
                    "agx_mcp.mcp_server.executeSql"
                ) as mock_sql:
                    mock_sql.return_value = []

                    with mock.patch(
                        "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                    ) as mock_validate:
                        mock_validate.return_value = (
                            "", step_map, validated_steps, entry_tasks
                        )

                        with mock.patch(
                            "agx_mcp.mcp_server._sync_entity"
                        ):
                            with mock.patch(
                                "agx_mcp.mcp_server._deploy_executions"
                            ) as mock_deploy:
                                mock_deploy.return_value = json.dumps(
                                    {"ok": True, "steps_deployed": 2}
                                )

                                result = json.loads(
                                    agx_book_orchestrate_multi_chapter(
                                        caller_agent_id="agt_test",
                                        book_root="/tmp/book",
                                        chapters=_make_chapters_json(chapters_list),
                                        parallel=True,
                                    )
                                )

        assert result["success"] is True
        assert result["parallel"] is True

    def test_empty_chapters(self):
        """An empty chapters list returns an error."""
        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()
        result = json.loads(
            agx_book_orchestrate_multi_chapter(
                caller_agent_id="agt_test",
                book_root="/tmp/book",
                chapters="[]",
            )
        )
        assert result["success"] is False
        assert "非空" in str(result.get("errors", ""))

    def test_invalid_json_chapters(self):
        """A non-JSON chapters argument returns an error."""
        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()
        result = json.loads(
            agx_book_orchestrate_multi_chapter(
                caller_agent_id="agt_test",
                book_root="/tmp/book",
                chapters="not json",
            )
        )
        assert result["success"] is False

    def test_sub_plan_failure(self):
        """One sub-plan fails: an error is returned listing the sub-plans already generated."""
        chapters_list = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test"},
        ]

        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()

        with mock.patch(
            "agx_mcp.mcp_server.agx_book_orchestrate_chapter"
        ) as mock_sub:
            # the first succeeds, the second fails
            mock_sub.side_effect = [
                _mock_sub_orch_run_success("plan_ch03_test123"),
                json.dumps({"success": False, "errors": ["文件缺失"]}),
            ]

            result = json.loads(
                agx_book_orchestrate_multi_chapter(
                    caller_agent_id="agt_test",
                    book_root="/tmp/book",
                    chapters=_make_chapters_json(chapters_list),
                )
            )

        assert result["success"] is False
        assert result["sub_plan_ids"] == ["plan_ch03_test123"]
        assert "sub_plan_errors" in str(result.get("errors", ""))

    def test_duplicate_master_plan(self):
        """A duplicate master plan_id returns an error."""
        chapters_list = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test"},
        ]
        master_plan = _mock_master_plan_json()
        validated_steps = master_plan["steps"]
        step_map = {s["run_uuid"]: s for s in validated_steps}
        entry_tasks = [s["run_uuid"] for s in validated_steps]

        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()

        with mock.patch(
            "agx_mcp.mcp_server.agx_book_orchestrate_chapter"
        ) as mock_sub:
            mock_sub.return_value = _mock_sub_orch_run_success("plan_ch03_test123")

            with mock.patch(
                "tools.book_orchestrate.MultiChapterOrchestrator"
            ) as MockMC:
                mock_mc = MockMC.return_value
                mock_mc.build_master_plan.return_value = master_plan

                with mock.patch(
                    "agx_mcp.mcp_server.executeSql"
                ) as mock_sql:
                    mock_sql.return_value = [{"1": 1}]  # the existence check hits

                    with mock.patch(
                        "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                    ) as mock_validate:
                        mock_validate.return_value = (
                            "", step_map, validated_steps, entry_tasks
                        )

                        result = json.loads(
                            agx_book_orchestrate_multi_chapter(
                                caller_agent_id="agt_test",
                                book_root="/tmp/book",
                                chapters=_make_chapters_json(chapters_list),
                            )
                        )

        assert "error" in result
        assert "already exists" in result.get("error", "")

    def test_deploy_failure(self):
        """_deploy_executions fails: an error is returned."""
        chapters_list = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test"},
        ]
        master_plan = _mock_master_plan_json()
        validated_steps = master_plan["steps"]
        step_map = {s["run_uuid"]: s for s in validated_steps}
        entry_tasks = [s["run_uuid"] for s in validated_steps]

        agx_book_orchestrate_multi_chapter = _load_ao_book_orchestrate_multi_chapter()

        with mock.patch(
            "agx_mcp.mcp_server.agx_book_orchestrate_chapter"
        ) as mock_sub:
            mock_sub.return_value = _mock_sub_orch_run_success("plan_ch03_test123")

            with mock.patch(
                "tools.book_orchestrate.MultiChapterOrchestrator"
            ) as MockMC:
                mock_mc = MockMC.return_value
                mock_mc.build_master_plan.return_value = master_plan

                with mock.patch(
                    "agx_mcp.mcp_server.executeSql"
                ) as mock_sql:
                    mock_sql.return_value = []

                    with mock.patch(
                        "agx_mcp.mcp_server._validate_and_expand_plan_steps"
                    ) as mock_validate:
                        mock_validate.return_value = (
                            "", step_map, validated_steps, entry_tasks
                        )

                        with mock.patch(
                            "agx_mcp.mcp_server._sync_entity"
                        ):
                            with mock.patch(
                                "agx_mcp.mcp_server._deploy_executions"
                            ) as mock_deploy:
                                mock_deploy.return_value = json.dumps(
                                    {"ok": False, "error": "DB deadlock"}
                                )

                                result = json.loads(
                                    agx_book_orchestrate_multi_chapter(
                                        caller_agent_id="agt_test",
                                        book_root="/tmp/book",
                                        chapters=_make_chapters_json(chapters_list),
                                    )
                                )

        assert result.get("ok") is False or "error" in result


# ── dynamically load agx_book_orchestrate_multi_chapter ──

def _load_ao_book_orchestrate_multi_chapter():
    """
    Load agx_book_orchestrate_multi_chapter.

    It relies on _load_ao_book_orchestrate_chapter() having mocked the
    environment first. Import straight from agx_mcp.mcp_server (the module is
    already cached).
    """
    from agx_mcp.mcp_server import agx_book_orchestrate_multi_chapter
    return agx_book_orchestrate_multi_chapter
