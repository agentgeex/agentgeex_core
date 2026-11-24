"""
Clone identity adaptation & deploy memory isolation tests (2026-09-28)

Covers the approved remediation:
  1) _clone_agent_from_template renders the inherited *_md files at clone time
     (agents/identity/soul/tools/user)
     - placeholder template {{AGENT_NAME}} -> instance value
     - hard-coded legacy template -> conservative rewrite of the first-line
       title plus the first name line
  2) agx_deploy_agent isolates memory on first deployment: MEMORY.md and
     memory/ are moved into .memory_quarantine_<ts>/

Layers:
  - unit (no DB): import the pure functions from agx_mcp.mcp_server directly
  - integration (through MCP): agx_upsert_agent (build a template) ->
    agx_clone_agent -> agx_deploy_agent
"""

import json
import os
import shutil

import pytest

from conftest import HOST_ID

pytestmark = pytest.mark.lifecycle


# ═══════════════════════════════════════════════════════════════
# import the pure function under test directly (no MCP startup)
# ═══════════════════════════════════════════════════════════════


def _import_render():
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(root, "agx_mcp"), root):
        if p not in sys.path:
            sys.path.insert(0, p)
    import agx_mcp.mcp_server as m

    return m


MAP = {
    "AGENT_ID": "aifr_pm",
    "AGENT_NAME": "aifr_pm",
    "AGENT_ROLE": "project_manager",
    "AGENT_MODEL": "deepseek",
    "HOST_ID": HOST_ID,
    "PROJECT_ID": "proj_x",
}


# ── 1.1 placeholder template ──


def test_render_placeholder_template():
    m = _import_render()
    src = (
        "# AGENTS.md — {{AGENT_NAME}} 工作空间\n\n"
        "这是 `{{AGENT_NAME}}` 的工作空间。\n\n"
        "- **名称:** {{AGENT_NAME}}\n"
        "- **角色:** {{AGENT_ROLE}}\n"
        "- **模型:** {{AGENT_MODEL}}\n"
        "- **宿主主机:** {{HOST_ID}}\n"
        "- **项目:** {{PROJECT_ID}}\n"
    )
    out = m._render_agent_md_for_identity(src, MAP)
    assert "{{" not in out, "占位符未全部替换"
    assert "aifr_pm" in out
    assert "project_manager" in out
    assert HOST_ID in out
    assert "proj_x" in out
    # rewrite the title with the new name
    assert out.splitlines()[0] == "# AGENTS.md — aifr_pm 工作空间"
    # in-body identity references are replaced too (this is not a hard-coded template)
    assert "这是 `aifr_pm` 的工作空间。" in out


def test_render_is_idempotent():
    m = _import_render()
    src = "# AGENTS.md — {{AGENT_NAME}} 工作空间\n\n- **名称:** {{AGENT_NAME}}\n正文保留\n"
    once = m._render_agent_md_for_identity(src, MAP)
    twice = m._render_agent_md_for_identity(once, MAP)
    assert once == twice, "渲染必须幂等"
    # the headless case (a name line has to be inserted) must stay idempotent
    h = "# AGENTS.md — 旧名工作空间\n\n## 会话启动\n正文\n"
    h1 = m._render_agent_md_for_identity(h, MAP)
    h2 = m._render_agent_md_for_identity(h1, MAP)
    assert h1 == h2, "插入名称行后二次渲染必须幂等"


# ── 1.2 hard-coded legacy template (super_pm case) ──


def test_render_hardcoded_old_template_title_and_name():
    m = _import_render()
    src = (
        "# AGENTS.md — super_pm（及其克隆agent） 工作空间\n\n"
        "> **我是谁？** 详见 IDENTITY.md\n\n"
        "这是 `super_pm` 的工作空间。\n\n"
        "- **名称: super_pm\n"
        "- **角色: project_manager\n"
        "正文：super_pm 负责…\n"
    )
    out = m._render_agent_md_for_identity(src, MAP)
    lines = out.splitlines()
    # rewrite the first-line title (using agent_id)
    assert lines[0] == "# AGENTS.md — aifr_pm 工作空间", lines[0]
    # rewrite the first name line; when id == name write the id only
    assert any("**名称: aifr_pm**" in ln for ln in lines[:8]), out
    # do not touch body references to the old name (conservative: only the leading identity block)
    assert "正文：super_pm 负责…" in out


def test_render_name_cell_with_display_name():
    """When agent_name is a localised display name, the name line becomes agent_id (agent_name)."""
    m = _import_render()
    src = "# AGENTS.md — 旧模板 工作空间\n\n- **名称: 旧名\n"
    mp = dict(MAP, AGENT_NAME="图书读者试读员")
    out = m._render_agent_md_for_identity(src, mp)
    assert "**名称: aifr_pm (图书读者试读员)**" in out
    # the title still uses agent_id (never the localised name)
    assert out.splitlines()[0] == "# AGENTS.md — aifr_pm 工作空间"


def test_render_headless_template_inserts_name_line():
    """A legacy template without a name line (the book_* family) -> insert one before the first H2"""
    m = _import_render()
    src = (
        "# AGENTS.md — 图书读者试读员工作空间\n\n"
        "> ⚠️ **模板身份**：我是模板 Agent。\n\n"
        "---\n\n"
        "## 会话启动\n\n1. 读取 SOUL.md\n"
    )
    out = m._render_agent_md_for_identity(src, MAP)
    lines = out.splitlines()
    assert lines[0] == "# AGENTS.md — aifr_pm 工作空间"
    assert any(ln.strip().startswith("- **名称: aifr_pm") for ln in lines[:10]), out
    # the insertion point must precede the first H2
    h2 = next(i for i, ln in enumerate(lines) if ln.startswith("## "))
    ni = next(i for i, ln in enumerate(lines) if ln.strip().startswith("- **名称"))
    assert ni < h2


def test_render_name_line_markdown_bold_variant():
    """Also accepts the legacy bold-label name-line form"""
    m = _import_render()
    src = "# AGENTS.md — 旧模板 工作空间\n\n- **名称:** expo_developer\n- **角色:** worker\n"
    out = m._render_agent_md_for_identity(src, MAP)
    assert "**名称:** aifr_pm**" in out


def test_render_empty_text_noop():
    m = _import_render()
    assert m._render_agent_md_for_identity("", MAP) == ""
    assert m._render_agent_md_for_identity(None, MAP) is None


def test_render_does_not_touch_non_identity_doc():
    """Text that does not follow the AGENTS.md style (no title, no name line) is returned unchanged"""
    m = _import_render()
    src = "我是一个普通说明文档。\n名称不是身份行。\n"
    assert m._render_agent_md_for_identity(src, MAP) == src


# ═══════════════════════════════════════════════════════════════
# 2. memory isolation (pure functions, temp workspace)
# ═══════════════════════════════════════════════════════════════


def test_quarantine_moves_memory_and_dir(tmp_path):
    m = _import_render()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("旧记忆", encoding="utf-8")
    memdir = ws / "memory"
    memdir.mkdir()
    (memdir / "2026-06-25.md").write_text("日记", encoding="utf-8")
    (ws / "AGENTS.md").write_text("身份", encoding="utf-8")

    res = m._quarantine_workspace_memory(str(ws))

    assert set(res["quarantined"]) == {"MEMORY.md", "memory"}
    assert res["dir"] and os.path.isdir(res["dir"])
    # the original location has been emptied
    assert not (ws / "MEMORY.md").exists()
    assert not (ws / "memory").exists()
    # fully preserved inside the quarantine directory (moved, not deleted)
    assert os.path.isfile(os.path.join(res["dir"], "MEMORY.md"))
    assert os.path.isfile(os.path.join(res["dir"], "memory", "2026-06-25.md"))
    # non-memory files are untouched
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "身份"


def test_quarantine_noop_when_no_memory(tmp_path):
    m = _import_render()
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("x", encoding="utf-8")
    res = m._quarantine_workspace_memory(str(ws))
    assert res["quarantined"] == []
    assert res["skipped"] == "no-memory-files"
    assert not any(d.startswith(".memory_quarantine_") for d in os.listdir(ws))


def test_quarantine_idempotent_reuses_dir(tmp_path):
    m = _import_render()
    ws = tmp_path / "ws3"
    ws.mkdir()
    (ws / "MEMORY.md").write_text("a", encoding="utf-8")
    r1 = m._quarantine_workspace_memory(str(ws))
    (ws / "MEMORY.md").write_text("b", encoding="utf-8")
    r2 = m._quarantine_workspace_memory(str(ws))
    assert r1["dir"] == r2["dir"], "应复用同一隔离目录"
    qdirs = [d for d in os.listdir(ws) if d.startswith(".memory_quarantine_")]
    assert len(qdirs) == 1


# ═══════════════════════════════════════════════════════════════
# 3. integration: clone -> deploy (through MCP)
# ═══════════════════════════════════════════════════════════════

# ★ 2026-10-25 layout change: ~/.agx/openclaw_agents -> ~/.agx/agx_agents (the legacy layout is still supported)
def _ws_root() -> str:
    for _wd in ("~/.agx", "~/.agx"):
        for _sub in ("agx_agents", "openclaw_agents"):
            _p = os.path.expanduser(os.path.join(_wd, _sub))
            if os.path.isdir(_p):
                return _p
    return os.path.expanduser("~/.agx/agx_agents")


_WS_ROOT = _ws_root()


def _clone_agents_md(mysql, agent_id):
    rows = mysql(f"SELECT agents_md FROM agx_agent_registry WHERE agent_id='{agent_id}'")
    return rows[0]["agents_md"] if rows else ""


def test_integration_clone_renders_agents_md(mcp, mysql, host_id, network_id):
    """Cloning a hard-coded legacy template -> the clone agents_md must contain the new agent_id and not the template name"""
    tmpl = "pytest_clone_tmpl"
    new = "pytest_clone_child"
    old_md = (
        "# AGENTS.md — super_pm（及其克隆agent） 工作空间\n\n"
        "- **名称: super_pm\n"
        "- **角色: project_manager\n"
        "正文保留 super_pm 其他内容\n"
    )
    # build a template (worker role avoids triggering admin backfill; use a fake agent_labs)
    mcp(
        "agx_upsert_agent",
        caller_agent_id="agx_core",
        network_id=network_id,
        agent_id=tmpl,
        agent_name=tmpl,
        role="worker",
        agent_labs="pytest_clone",
        host_id=host_id,
        model="deepseek",
        agents_md=old_md,
    )
    # clear possible leftovers first
    mcp("agx_update_agent", caller_agent_id="agx_core", agent_id=new, updates={"active_status": "0"})

    r = mcp(
        "agx_clone_agent",
        caller_agent_id="agx_agentfather",
        host_id=host_id,
        template_agent_id=tmpl,
        target_project_id="system_project",
        new_agent_id=new,
        agent_name=new,
    )
    assert new in str(r), f"clone failed: {r}"

    md = _clone_agents_md(mysql, new)
    assert md, "克隆体 agents_md 为空"
    assert md.splitlines()[0] == f"# AGENTS.md — {new} 工作空间", md.splitlines()[0]
    assert "**名称: " + new in md, md[:200]
    # the template name no longer appears in the identity block
    assert "super_pm" not in md.split("正文保留")[0], "身份块仍残留模板名"
    # the body is conservatively preserved
    assert "正文保留 super_pm 其他内容" in md

    # cleanup
    mcp("agx_update_agent", caller_agent_id="agx_core", agent_id=new, updates={"active_status": "0"})
    mcp("agx_update_agent", caller_agent_id="agx_core", agent_id=tmpl, updates={"active_status": "0"})


def test_integration_deploy_quarantines_memory(mcp, mysql, host_id, network_id):
    """The first deploy must move the workspace MEMORY.md and memory/ into the quarantine directory"""
    aid = "pytest_deploy_mem"
    ws = os.path.join(_WS_ROOT, aid, "workspace")
    # first build a clean deploy-capable agent
    mcp(
        "agx_upsert_agent",
        caller_agent_id="agx_core",
        network_id=network_id,
        agent_id=aid,
        agent_name=aid,
        role="worker",
        agent_labs="pytest_deploy",
        host_id=host_id,
        model="deepseek",
        agents_md="# AGENTS.md — pytest_deploy_mem 工作空间\n\n- **名称: pytest_deploy_mem\n",
    )
    try:
        # seed some leftover memory
        os.makedirs(os.path.join(ws, "memory"), exist_ok=True)
        with open(os.path.join(ws, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("遗留记忆")
        with open(os.path.join(ws, "memory", "2026-01-01.md"), "w", encoding="utf-8") as f:
            f.write("遗留日记")

        r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id=aid, force=True)
        d = r if isinstance(r, dict) else json.loads(r)
        assert d.get("status") == "succeeded", d
        mq = d.get("memory_quarantine", {})
        assert set(mq.get("quarantined", [])) == {"MEMORY.md", "memory"}, mq
        # outcome: the original location is empty, the quarantine directory is preserved
        assert not os.path.exists(os.path.join(ws, "MEMORY.md"))
        assert not os.path.exists(os.path.join(ws, "memory"))
        assert os.path.isfile(os.path.join(mq["dir"], "MEMORY.md"))
        assert os.path.isfile(os.path.join(mq["dir"], "memory", "2026-01-01.md"))
        # AGENTS.md has landed
        assert os.path.isfile(os.path.join(ws, "AGENTS.md"))
    finally:
        # clean up disk + DB
        shutil.rmtree(os.path.join(_WS_ROOT, aid), ignore_errors=True)
        mcp("agx_update_agent", caller_agent_id="agx_core", agent_id=aid, updates={"active_status": "0"})
