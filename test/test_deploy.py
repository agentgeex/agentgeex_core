"""
agx_deploy_agent test cases
"""

import json
import os


def test_deploy_pt2_pm_force(mcp, host_id):
    """Force-redeploy pt2_pm (project_manager) and verify the status is succeeded"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="pt2_pm", force=True)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "succeeded", f"status: {d.get('status')}, error: {d.get('error', d)}"
    assert d["agent_id"] == "pt2_pm"
    assert d["host_id"] == host_id
    assert "AGENTS.md" in d["files_written"]
    assert "agx.env" in d["files_written"]

    # verify the files landed on disk
    ws = d["workspace"]
    env_path = os.path.join(ws, "agx.env")
    assert os.path.isfile(env_path), f"{env_path} missing"
    with open(env_path) as f:
        content = f.read()
    assert "export AGX_AGENT_ID" in content
    assert "export AGX_PROJECT_ID" in content


def test_deploy_skip_if_already_deployed(mcp):
    """An already-deployed agent is skipped by default"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="pt2_pm", force=False)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "skipped", f"expected skipped, got {d.get('status')}: {d}"
    assert "跳过" in d.get("reason", "")


def test_deploy_unknown_agent(mcp):
    """A non-existent agent should return failed"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="nonexistent_agent_xyz", force=False)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "failed", "expected failed for unknown agent"
    assert "不" in d.get("error", "")


def test_deploy_wrong_host(mcp, host_agent_id):
    """An agent owned by another host should return failed"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="agx_host_29bf48", force=False)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "failed"
    assert "不" in d.get("error", "")


def test_deploy_verify_core_files_nonempty(mcp, host_id):
    """Core files (AGENTS.md, agx.env) are non-empty after deployment"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="pt2_pm", force=True)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "succeeded"
    v = d.get("verification", {})
    assert v.get("file_AGENTS.md") is True, f"AGENTS.md 应为非空: {v}"
    assert v.get("file_agx.env") is True, f"agx.env 应为非空: {v}"


def test_deploy_verify_env_content(mcp, host_id):
    """agx.env contains the key fields after deployment"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="pt2_pm", force=True)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "succeeded"
    v = d.get("verification", {})
    assert v.get("env_has_agent_id") is True, f"env_has_agent_id: {v}"
    assert v.get("env_has_project_id") is True, f"env_has_project_id: {v}"


def test_deploy_optional_files_allowed_empty(mcp, host_id):
    """Non-core files (SOUL/IDENTITY etc.) may have empty DB fields (size=0) without affecting deployment success"""
    r = mcp("agx_deploy_agent", caller_agent_id="agx_core", agent_id="pt2_pm", force=True)
    d = r if isinstance(r, dict) else json.loads(r)
    assert d["status"] == "succeeded", f"空字段不应阻止部署: {d}"
    v = d.get("verification", {})
    # optional fields such as SOUL/IDENTITY: the file only has to exist (not necessarily be non-empty)
    for fname in ("SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md", "NOTEBOOK.md"):
        assert v.get(f"file_{fname}") is not None, f"verification 缺少 file_{fname}: {v}"
