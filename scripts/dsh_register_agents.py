#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh_register_agents.py - batch-register AgentGeeX agent directories into DSH (DeepSeek-Harness).

Design (agreed with the owner on 2026-10-16; rewritten for the DSH 0.2.2 protocol on 2026-11-16)
==============================================================
An AgentGeeX agent identity is a directory; DSH uses a workspace to model "a directory is an agent".
Registration = create one workspace per agent directory and sync its title to agent_id.

** Why the whole file was rewritten on 2026-11-16 **
The old implementation returned 404 for everything on DSH 0.2.2-rc.1; all three places were broken:
  1. Method names used dots (`workspace.create`) - 0.2.2 only accepts slashes (`workspace/create`);
  2. The envelope was a bare payload - 0.2.2 expects `{"payload":{"args":{"request":{...}}}}`;
  3. **No authentication** - the web surface returns 401 for anonymous requests (measured).
The old route of "call `workspace.list` first, then match by path" is gone as well:
0.2.2 has **no workspace/list** (404), but `workspace/create` is itself idempotent
(it returns `created:false` plus the existing workspace when present), so list is unnecessary.

-> Protocol and auth are both delegated to `agx_dsh_sdk_client.DshWebAdmin` (shared with the bridge,
  so the envelope/cookie logic is not copied here - a copy would be one more thing that can drift).

Idempotency strategy (after the rewrite)
================
For each `<root>/<agent_id>/workspace`:
  1. `workspace/create {path}`   -> get workspaceId and the created flag (existing ones are not rebuilt)
  2. when title != agent_id      -> `workspace/rename {workspaceId, title=agent_id}`
  3. either step fails           -> record failed, continue with the next one (do not abort the batch)

Usage
====
  python3 dsh_register_agents.py --dry-run        # print the plan only, no DSH changes
  python3 dsh_register_agents.py                  # actually register (local default 127.0.0.1:8791)
  python3 dsh_register_agents.py --host 10.0.0.13 --port 20790
  python3 dsh_register_agents.py --root /home/owner/.agx/agx_agents
  python3 dsh_register_agents.py --token <TOKEN>   # or env var AGX_DSH_TOKEN
  python3 dsh_register_agents.py --token-file /path/to/token   # recommended: keeps it out of argv/env

Auth: the `?token=` value printed when `dsh web` starts. It can also be written to the config file as
`dsh.token` or `dsh.token_file` (DshWebAdmin resolves in the order: argument -> env -> config).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agx_dsh_sdk_client import DshWebAdmin, DshWebAdminError  # noqa: E402


# * Directory layout: ~/.agx/agx_agents
#   Existence on disk is the criterion; on this host (test_host_mac) the agent directory is already a symlink into the central repo.
def _default_root() -> str:
    home = Path.home()
    for wd in (".agx",):
        for sub in ("agx_agents", "openclaw_agents"):
            p = home / wd / sub
            if p.is_dir():
                return str(p)
    return str(home / ".agx" / "agx_agents")


DEFAULT_ROOT = _default_root()


def discover_agents(root: str) -> list:
    """Scan <root>/*/workspace and return [(agent_id, workspace_path)]. agent_id is the directory name."""
    root_p = Path(os.path.expanduser(root))
    out = []
    if not root_p.is_dir():
        return out
    for agent_dir in sorted(root_p.iterdir()):
        if not agent_dir.is_dir():
            continue
        ws = agent_dir / "workspace"
        if ws.is_dir():
            out.append((agent_dir.name, str(ws)))
    return out


def main():
    ap = argparse.ArgumentParser(description="把 AgentGeeX agent 目录批量注册进 DSH workspace")
    ap.add_argument("--host", default=os.environ.get("AGX_DSH_HOST", "127.0.0.1"),
                    help="dsh web 监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AGX_DSH_PORT", "8791")),
                    help="dsh web 端口（默认 8791）")
    ap.add_argument("--token", default="", help="dsh web 的 token（也可用 AGX_DSH_TOKEN）")
    ap.add_argument("--token-file", default="",
                    help="从文件读 token（文件内只有 token 一行）——避免 token 进 argv/环境变量")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"agent 根目录（默认 {DEFAULT_ROOT}）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改 DSH")
    ap.add_argument("--only", default="",
                    help="只注册这些 agent_id（逗号分隔）。空=扫到几个注册几个（旧行为）")
    ap.add_argument("--prune", action="store_true",
                    help="先删掉「不在 --only 白名单里」的 workspace 再注册。"
                         "必须与 --only 同用（防止误删全部）。"
                         "0.2.2 没有 workspace/list，故读 ~/.dsh/storages/workspace.json 取现状。")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    print(f"[DSH 注册] web={base}")
    print(f"[DSH 注册] agent 根={args.root}")
    print()

    agents = discover_agents(args.root)
    print(f"发现 agent 目录: {len(agents)} 个")
    if args.only.strip():
        want = [x.strip() for x in args.only.split(",") if x.strip()]
        before = len(agents)
        keepset = set(want)
        agents = [(a, p) for a, p in agents if a in keepset]
        print(f"  按 --only 过滤: {before} -> {len(agents)} 个（白名单 {len(want)} 个）")
        missing = [w for w in want if w not in {a for a, _ in agents}]
        if missing:
            print(f"  WARN 目录不存在，跳过: {missing}")
    if not agents:
        print("（没有可注册的目录，退出）")
        return

    if args.dry_run:
        for agent_id, path in agents:
            print(f"  [PLAN] workspace/create + rename → title={agent_id}   path={path}")
        print()
        print("(dry-run，未实际改动；0.2.2 无 workspace/list，故无法预判已存在，"
              "实际执行时以 create 返回的 created 标志计数)")
        return

    token = args.token
    if not token and args.token_file:
        with open(os.path.expanduser(args.token_file), "r", encoding="utf-8") as f:
            token = f.read().strip()
    admin = DshWebAdmin(base_url=base, token=token)

    # -- prune: first delete workspaces that are not in the allowlist --
    # Why read a file: 0.2.2 has no workspace/list (404), so the current list cannot be fetched.
    # workspace.json is the persistent state of dsh web; delete updates both memory and disk.
    if args.prune:
        if not args.only.strip():
            print("  FAIL --prune 必须与 --only 一起用（否则等于「删光」）")
            return
        import json as _json
        wsjson = os.path.expanduser("~/.dsh/storages/workspace.json")
        try:
            state = _json.load(open(wsjson, encoding="utf-8"))
        except Exception as e:
            print(f"  FAIL 读不到 {wsjson}: {e}")
            return
        keep = {x.strip() for x in args.only.split(",") if x.strip()}
        tables = (state.get("tables") or {}).get("workspaces") or {}
        drop = [(wid, str(v.get("title") or ""))
                for wid, v in tables.items() if str(v.get("title") or "") not in keep]
        print(f"  [prune] 现有 workspace {len(tables)} 个，待删 {len(drop)} 个")
        removed = 0
        for wid, title in drop:
            try:
                admin.call("workspace/delete", {"workspaceId": wid})
                removed += 1
            except DshWebAdminError as e:
                if "not-found" in str(e).lower() or "not found" in str(e).lower():
                    removed += 1
                else:
                    print(f"    FAIL 删除 {title}: {e}")
        print(f"  [prune] 已删 {removed}/{len(drop)}")
        if removed != len(drop):
            print("  WARN 有删除失败，继续执行注册")

    created = renamed = existed = failed = 0
    for agent_id, path in agents:
        try:
            v = admin.call("workspace/create", {"path": path})
        except DshWebAdminError as e:
            print(f"  ✗ FAIL(create)  {agent_id}: {e}")
            failed += 1
            continue
        ws = v.get("workspace") or {}
        wid = str(ws.get("workspaceId") or "")
        is_new = bool(v.get("created"))
        if not wid:
            print(f"  ✗ FAIL(create)  {agent_id}: 未返回 workspaceId")
            failed += 1
            continue

        cur_title = ws.get("title")
        if cur_title == agent_id:
            print(f"  · SKIP  {agent_id}  (wid={wid}, title 已一致)")
            existed += 1
            continue
        try:
            admin.call("workspace/rename", {"workspaceId": wid, "title": agent_id})
        except DshWebAdminError as e:
            print(f"  ✗ FAIL(rename)  {agent_id}: {e}")
            failed += 1
            continue
        if is_new:
            created += 1
            print(f"  ✓ CREATE+RENAME  {agent_id}  (wid={wid}, {cur_title!r} → {agent_id!r})")
        else:
            renamed += 1
            print(f"  ✓ RENAME  {agent_id}  (wid={wid}, {cur_title!r} → {agent_id!r})")

    print()
    print(f"[完成] create+rename={created}  rename={renamed}  "
          f"skip={existed}  fail={failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
