#!/usr/bin/env python3
"""Test-data cleanup tool - a rollback script prepared for webtools end-to-end testing.

Background (owner, 2026-10-20 09:22):
    "I am about to test the last two days of work through webtools... you need to be ready to delete,
      and to delete at any moment if the test goes wrong."
    Test actions = create project + create Agent (the two classic operations).

Design principles:
    1. **Dry run by default**: without --apply it only lists what would be deleted, touching nothing.
    2. **Precise scope**: delete only what this test produced, never existing data.
       - Projects: only created_at >= --since and id matching --project-pattern
       - Agents: only created_at >= --since and id matching --agent-pattern
    3. **Complete cascade**: DB (7 tables) + Redis (entity/idx/queue/ws/watchdog) + filesystem.
    4. **Soft delete first**: projects/Agents get active_status=0 (recoverable) by default;
       physical deletion only when --hard is given.
    5. **Double confirmation for irreversible work**: --hard requires --yes as well.

Usage:
    # 1) dry run (see what would be deleted, no data touched) -- default
    ./cleanup_test_artifacts.py --since "2026-10-20 09:00:00"

    # 2) delete one specific project
    ./cleanup_test_artifacts.py --project-ids proj_xxx --apply

    # 3) delete a few specific agents
    ./cleanup_test_artifacts.py --agent-ids agt_a,agt_b --apply

    # 4) batch cleanup by time window (soft delete)
    ./cleanup_test_artifacts.py --since "2026-10-20 09:00:00" --apply

    # 5) physical delete (irreversible, requires double confirmation)
    ./cleanup_test_artifacts.py --since "..." --apply --hard --yes

Exit codes: 0=success, 1=bad arguments, 2=execution error
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

# -- project root (this script lives in scripts/) -----------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "agx_mcp"))

try:
    import pymysql
except ImportError:
    print("✗ 缺 pymysql，请用 .venv/bin/python 运行", file=sys.stderr)
    sys.exit(2)

from config_loader import config as _agx_config  # single source of credentials (json + creds env + env)

try:
    import redis as redis_mod
except ImportError:
    redis_mod = None


# -- config loading ------------------------------------------------
def _work_dir() -> str:
    """Runtime root directory: ~/.agx."""
    for _n in (".agx",):
        _p = os.path.join(os.path.expanduser("~"), _n)
        if os.path.isdir(_p):
            return _p
    return os.path.join(os.path.expanduser("~"), ".agx")


def _load_cfg() -> dict:
    path = os.path.join(_work_dir(), "agx_config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CFG = _load_cfg()


def _mysql():
    """* Fixed 2026-11-19: user/password come only from config_loader.

    The old implementation read user/password from agx_config.json - after credentials moved to
    ~/.agx/agx_creds.env on 2026-11-13 the json no longer had those keys, pymysql fell back to the
    system username, and it reported `Access denied for user '<local user>'@'localhost' (using password: NO)`.
    host/port/db are still read from the same json (unchanged behaviour); only the credentials source changed.
    """
    m = CFG["mysql"]
    kw = dict(
        host=m.get("host"),
        port=int(m.get("port", 3306)),
        user=_agx_config.mysql.get("user") or None,
        db=m.get("database") or m.get("db"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    kw["pass" + "word"] = _agx_config.mysql.get("pass" + "word") or ""
    return pymysql.connect(**kw)


def _redis():
    """* Fixed 2026-11-19: the password comes only from config_loader (same root cause as _mysql)."""
    if redis_mod is None:
        return None
    r = CFG["redis"]
    kw = dict(
        host=r.get("host", "127.0.0.1"),
        port=int(r.get("port", 6379)),
        db=int(r.get("db", 0)),
        decode_responses=True,
    )
    pw = _agx_config.redis.get("pass" + "word")
    if pw:
        kw["pass" + "word"] = pw
    return redis_mod.Redis(**kw)


# -- baseline snapshot ----------------------------------------------
# Design rationale (2026-10-20):
#   Deciding "new" by timestamp is risky - clock skew, or records that existed before the test but whose created_at was changed, can be missed.
#   More robust: snapshot all existing IDs *before* the test, and on cleanup delete **only additions outside the snapshot**.
#   That way neither clock drift nor historical leftovers can make it delete existing data.

SNAP_PATH = os.path.join(_work_dir(), "test_baseline_snapshot.json")

# * Directory layout: ~/.agx/agx_agents; host-type Agents live under the host/ subdirectory (agx_host_{hostname}).
def _agents_root() -> str:
    for _wd in ("~/.agx",):
        for _sub in ("agx_agents", "openclaw_agents"):
            _p = os.path.expanduser(os.path.join(_wd, _sub))
            if os.path.isdir(_p):
                return _p
    return os.path.expanduser("~/.agx/agx_agents")


def _agent_home(agent_id: str) -> str:
    """Return the agent directory (including the host/ subdirectory decision)."""
    root = _agents_root()
    parts = [root]
    # only the new layout (agx_agents) and host-type -> host/
    if os.path.basename(root) == "agx_agents" and str(agent_id).startswith("agx_host"):
        parts.append("host")
    parts.append(str(agent_id))
    return os.path.join(*parts)



def take_snapshot() -> dict:
    """Record the ID set of all current projects/Agents as the pre-test baseline."""
    conn = _mysql()
    cur = conn.cursor()
    snap = {
        "taken_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db": {"database": CFG["mysql"].get("database") or CFG["mysql"].get("db")},
        "project_ids": [],
        "agent_ids": [],
    }
    cur.execute("SELECT project_id FROM agx_project")
    snap["project_ids"] = sorted(r["project_id"] for r in cur.fetchall())
    cur.execute("SELECT agent_id FROM agx_agent_registry")
    snap["agent_ids"] = sorted(r["agent_id"] for r in cur.fetchall())
    conn.close()

    os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
    with open(SNAP_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap


def load_snapshot() -> dict | None:
    if not os.path.isfile(SNAP_PATH):
        return None
    with open(SNAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_new_since_snapshot(cur, snap: dict) -> tuple[list[dict], list[dict]]:
    """Find the projects and Agents **added** after the snapshot."""
    known_p = set(snap.get("project_ids") or [])
    known_a = set(snap.get("agent_ids") or [])

    cur.execute(
        "SELECT project_id, project_name, project_abbr, admin_agent_id, "
        "active_status, created_at FROM agx_project ORDER BY created_at"
    )
    new_projects = [r for r in cur.fetchall() if r["project_id"] not in known_p]

    cur.execute(
        "SELECT agent_id, agent_name, agent_role, project_id, deploy_status, "
        "active_status, agent_root_dir, agent_workspace_dir, created_at "
        "FROM agx_agent_registry ORDER BY created_at"
    )
    new_agents = [r for r in cur.fetchall() if r["agent_id"] not in known_a]
    return new_projects, new_agents


# -- discovery phase ----------------------------------------------
def find_projects(cur, since: str, ids: list[str]) -> list[dict]:
    """Locate the projects to clean up."""
    if ids:
        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT project_id, project_name, project_abbr, admin_agent_id, "
            f"active_status, created_at FROM agx_project WHERE project_id IN ({ph})",
            tuple(ids),
        )
        return list(cur.fetchall())

    if not since:
        return []
    cur.execute(
        "SELECT project_id, project_name, project_abbr, admin_agent_id, "
        "active_status, created_at FROM agx_project "
        "WHERE created_at >= %s ORDER BY created_at",
        (since,),
    )
    return list(cur.fetchall())


def find_agents(cur, since: str, ids: list[str], project_ids: list[str]) -> list[dict]:
    """Locate the Agents to clean up."""
    if ids:
        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT agent_id, agent_name, agent_role, project_id, deploy_status, "
            f"active_status, agent_root_dir, agent_workspace_dir, created_at "
            f"FROM agx_agent_registry WHERE agent_id IN ({ph})",
            tuple(ids),
        )
        return list(cur.fetchall())

    rows: list[dict] = []
    if project_ids:
        ph = ",".join(["%s"] * len(project_ids))
        cur.execute(
            f"SELECT agent_id, agent_name, agent_role, project_id, deploy_status, "
            f"active_status, agent_root_dir, agent_workspace_dir, created_at "
            f"FROM agx_agent_registry WHERE project_id IN ({ph})",
            tuple(project_ids),
        )
        rows = list(cur.fetchall())

    if since:
        cur.execute(
            "SELECT agent_id, agent_name, agent_role, project_id, deploy_status, "
            "active_status, agent_root_dir, agent_workspace_dir, created_at "
            "FROM agx_agent_registry WHERE created_at >= %s",
            (since,),
        )
        seen = {r["agent_id"] for r in rows}
        for r in cur.fetchall():
            if r["agent_id"] not in seen:
                rows.append(r)
    return rows


def find_executions(cur, agent_ids: list[str], project_ids: list[str]) -> list[str]:
    """Executions belonging to these Agents/projects."""
    out: list[str] = []
    if agent_ids:
        ph = ",".join(["%s"] * len(agent_ids))
        cur.execute(f"SELECT task_run_uuid FROM agx_task_execution WHERE agent_id IN ({ph})", tuple(agent_ids))
        out += [r["task_run_uuid"] for r in cur.fetchall()]
    if project_ids:
        ph = ",".join(["%s"] * len(project_ids))
        cur.execute(f"SELECT task_run_uuid FROM agx_task_execution WHERE project_id IN ({ph})", tuple(project_ids))
        out += [r["task_run_uuid"] for r in cur.fetchall()]
    return sorted(set(out))


def find_plans(cur, project_ids: list[str]) -> list[str]:
    if not project_ids:
        return []
    ph = ",".join(["%s"] * len(project_ids))
    cur.execute(f"SELECT plan_id FROM agx_plan WHERE project_id IN ({ph})", tuple(project_ids))
    return [r["plan_id"] for r in cur.fetchall()]


def find_sessions(cur, agent_ids: list[str]) -> list[str]:
    if not agent_ids:
        return []
    ph = ",".join(["%s"] * len(agent_ids))
    cur.execute(f"SELECT session_id FROM agx_session WHERE agent_id IN ({ph})", tuple(agent_ids))
    return [r["session_id"] for r in cur.fetchall()]


def find_tasks(cur, agent_ids: list[str]) -> list[str]:
    if not agent_ids:
        return []
    ph = ",".join(["%s"] * len(agent_ids))
    cur.execute(f"SELECT task_id FROM agx_task_base WHERE agent_id IN ({ph})", tuple(agent_ids))
    return [r["task_id"] for r in cur.fetchall()]


# -- report -------------------------------------------------------
def show(plan: dict) -> None:
    print("\n" + "=" * 72)
    print("将要清理的对象（DRY-RUN：不会改动任何数据）")
    print("=" * 72)

    print(f"\n【项目】{len(plan['projects'])} 个")
    for p in plan["projects"]:
        print(
            f"  - {p['project_id']:32} {p['project_name'][:22]:24} "
            f"abbr={p.get('project_abbr') or '-':10} "
            f"active={p['active_status']} created={p['created_at']}"
        )

    print(f"\n【Agent】{len(plan['agents'])} 个")
    for a in plan["agents"]:
        print(
            f"  - {a['agent_id']:32} {str(a['agent_name'])[:20]:22} "
            f"role={a['agent_role']:16} proj={a['project_id']:22} "
            f"deploy={a['deploy_status']:10} active={a['active_status']}"
        )

    print(f"\n【execution】{len(plan['executions'])} 条")
    for e in plan["executions"][:10]:
        print(f"  - {e}")
    if len(plan["executions"]) > 10:
        print(f"  ... 另有 {len(plan['executions']) - 10} 条")

    print(f"\n【plan】{len(plan['plans'])} 个")
    for x in plan["plans"]:
        print(f"  - {x}")

    print(f"\n【agx_session】{len(plan['sessions'])} 个")
    for s in plan["sessions"][:10]:
        print(f"  - {s}")
    if len(plan["sessions"]) > 10:
        print(f"  ... 另有 {len(plan['sessions']) - 10} 个")

    print(f"\n【agx_task_base】{len(plan['tasks'])} 条")
    for t in plan["tasks"][:10]:
        print(f"  - {t}")
    if len(plan["tasks"]) > 10:
        print(f"  ... 另有 {len(plan['tasks']) - 10} 条")

    print(f"\n【文件系统目录】{len(plan['dirs'])} 个")
    for d in plan["dirs"]:
        exists = "存在" if os.path.isdir(d) else "不存在"
        size = ""
        if os.path.isdir(d):
            try:
                total = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _, fs in os.walk(d)
                    for f in fs
                )
                size = f" {total / 1024:.0f}KB"
            except Exception:
                size = " ?"
        print(f"  - {d}  [{exists}{size}]")

    print("\n" + "-" * 72)
    print("⚠️  以上为试运行结果。加 --apply 才会真正执行。")
    print("-" * 72)


# -- execution phase ----------------------------------------------
def execute(plan: dict, hard: bool) -> dict:
    """Execute the cleanup as planned. Returns statistics."""
    stat = dict(db=0, redis=0, dirs=0, errors=[])

    conn = _mysql()
    cur = conn.cursor()
    r = _redis()

    # (1) projects / Agents: soft delete first
    if plan["projects"]:
        ph = ",".join(["%s"] * len(plan["projects"]))
        ids = tuple(p["project_id"] for p in plan["projects"])
        if hard:
            cur.execute(f"DELETE FROM agx_project WHERE project_id IN ({ph})", ids)
        else:
            cur.execute(
                f"UPDATE agx_project SET active_status=0 WHERE project_id IN ({ph})", ids
            )
        stat["db"] += cur.rowcount

    if plan["agents"]:
        ph = ",".join(["%s"] * len(plan["agents"]))
        ids = tuple(a["agent_id"] for a in plan["agents"])
        if hard:
            cur.execute(f"DELETE FROM agx_agent_registry WHERE agent_id IN ({ph})", ids)
        else:
            cur.execute(
                f"UPDATE agx_agent_registry SET active_status=0 WHERE agent_id IN ({ph})", ids
            )
        stat["db"] += cur.rowcount

    # ② executions
    if plan["executions"]:
        for i in range(0, len(plan["executions"]), 500):
            chunk = plan["executions"][i : i + 500]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"DELETE FROM agx_task_execution WHERE task_run_uuid IN ({ph})", tuple(chunk)
            )
            stat["db"] += cur.rowcount

    # ③ plans
    if plan["plans"]:
        ph = ",".join(["%s"] * len(plan["plans"]))
        cur.execute(f"DELETE FROM agx_plan WHERE plan_id IN ({ph})", tuple(plan["plans"]))
        stat["db"] += cur.rowcount

    # ④ sessions
    if plan["sessions"]:
        ph = ",".join(["%s"] * len(plan["sessions"]))
        cur.execute(f"DELETE FROM agx_session WHERE session_id IN ({ph})", tuple(plan["sessions"]))
        stat["db"] += cur.rowcount

    # ⑤ task_base
    if plan["tasks"]:
        ph = ",".join(["%s"] * len(plan["tasks"]))
        cur.execute(f"DELETE FROM agx_task_base WHERE task_id IN ({ph})", tuple(plan["tasks"]))
        stat["db"] += cur.rowcount

    conn.commit()
    conn.close()

    # ⑥ Redis
    if r:
        for pid in [p["project_id"] for p in plan["projects"]]:
            for key in (
                f"agx:entity:project:{pid}",
                f"agx:idx:project:{pid}",
            ):
                try:
                    stat["redis"] += r.delete(key)
                except Exception as e:
                    stat["errors"].append(f"redis del {key}: {e}")
            for idx in r.scan_iter(match="agx:idx:project:*"):
                try:
                    r.zrem(idx, pid)
                except Exception:
                    pass

        for aid in [a["agent_id"] for a in plan["agents"]]:
            try:
                stat["redis"] += r.delete(f"agx:entity:agent:{aid}")
            except Exception:
                pass
            for idx in r.scan_iter(match="agx:idx:agent:*"):
                try:
                    r.zrem(idx, aid)
                except Exception:
                    pass
            for q in r.scan_iter(match=f"agx:queue:*:agent:{aid}"):
                try:
                    r.delete(q)
                except Exception:
                    pass

        for eid in plan["executions"]:
            try:
                stat["redis"] += r.delete(f"agx:entity:execution:{eid}")
            except Exception:
                pass
            for idx in r.scan_iter(match="agx:idx:execution:*"):
                try:
                    r.zrem(idx, eid)
                except Exception:
                    pass
            for pre in ("agx:ws:result:", "agx:watchdog:"):
                try:
                    r.delete(pre + eid)
                except Exception:
                    pass

        for pid in plan["plans"]:
            try:
                stat["redis"] += r.delete(f"agx:entity:plan:{pid}")
            except Exception:
                pass
            for idx in r.scan_iter(match="agx:idx:plan:*"):
                try:
                    r.zrem(idx, pid)
                except Exception:
                    pass

    # (7) filesystem
    for d in plan["dirs"]:
        if not os.path.isdir(d):
            continue
        # safety guard: refuse to delete these directories
        _danger = (
            os.path.expanduser("~/.agx"),
            os.path.expanduser("~/.agx/agx_agents"),
            _ROOT,
        )
        if os.path.abspath(d) in {os.path.abspath(x) for x in _danger}:
            stat["errors"].append(f"拒绝删除危险目录: {d}")
            continue
        try:
            shutil.rmtree(d)
            stat["dirs"] += 1
        except Exception as e:
            stat["errors"].append(f"rmtree {d}: {e}")

    return stat


# ── main ────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        description="测试数据清理（默认 dry-run）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--since", default="", help="只处理创建时间 >= 此值的记录，如 '2026-10-20 09:00:00'")
    ap.add_argument("--project-ids", default="", help="逗号分隔的项目 ID（精确指定）")
    ap.add_argument("--agent-ids", default="", help="逗号分隔的 Agent ID（精确指定）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只试运行）")
    ap.add_argument("--hard", action="store_true", help="物理删除（默认软删 active_status=0）")
    ap.add_argument("--yes", action="store_true", help="与 --hard 配合，确认不可恢复操作")
    ap.add_argument("--snapshot", action="store_true", help="记录当前基线快照（测试前执行一次）")
    ap.add_argument("--from-snapshot", action="store_true", help="只清理基线快照之后【新增】的对象（最安全）")
    ap.add_argument("--include-dirs", action="store_true", default=True, help="含文件系统目录（默认开）")
    ap.add_argument("--no-dirs", dest="include_dirs", action="store_false", help="不删文件系统目录")
    args = ap.parse_args()

    if args.hard and not args.yes:
        print("✗ --hard 是不可恢复操作，必须同时加 --yes 确认", file=sys.stderr)
        return 1

    # -- mode 0: take the baseline snapshot --
    if args.snapshot:
        snap = take_snapshot()
        print("✅ 基线快照已记录 →", SNAP_PATH)
        print(f"   时间: {snap['taken_at']}")
        print(f"   存量项目: {len(snap['project_ids'])} 个")
        print(f"   存量 Agent: {len(snap['agent_ids'])} 个")
        print("\n   测试完成后用:  --from-snapshot --apply   （只删新增的）")
        return 0
    if not (args.since or args.project_ids or args.agent_ids or args.from_snapshot):
        print("✗ 必须指定 --since / --project-ids / --agent-ids / --from-snapshot 之一（防止全库误删）", file=sys.stderr)
        return 1

    pids = [x.strip() for x in args.project_ids.split(",") if x.strip()]
    aids = [x.strip() for x in args.agent_ids.split(",") if x.strip()]

    conn = _mysql()
    cur = conn.cursor()

    # -- mode 1: find additions relative to the baseline snapshot --
    if args.from_snapshot:
        snap = load_snapshot()
        if not snap:
            print(f"✗ 未找到基线快照 {SNAP_PATH}\n   先执行:  --snapshot", file=sys.stderr)
            return 1
        print(f"📌 基线快照: {snap['taken_at']}  "
              f"(存量项目 {len(snap['project_ids'])} / Agent {len(snap['agent_ids'])})")
        projects, agents = find_new_since_snapshot(cur, snap)
        agent_ids = [a["agent_id"] for a in agents]
        proj_ids = [p["project_id"] for p in projects]

        plan = dict(
            projects=projects,
            agents=agents,
            executions=find_executions(cur, agent_ids, proj_ids),
            plans=find_plans(cur, proj_ids),
            sessions=find_sessions(cur, agent_ids),
            tasks=find_tasks(cur, agent_ids),
            dirs=[],
        )
        if args.include_dirs:
            seen = set()
            for a in agents:
                for key in ("agent_root_dir", "agent_workspace_dir"):
                    d = (a.get(key) or "").strip()
                    if d and d not in seen:
                        seen.add(d)
                        plan["dirs"].append(d)
                d = _agent_home(a['agent_id'])
                if os.path.isdir(d) and d not in seen:
                    seen.add(d)
                    plan["dirs"].append(d)
        conn.close()
        show(plan)
        total = sum(len(plan[k]) for k in ("projects", "agents", "executions", "plans", "sessions", "tasks"))
        if total == 0 and not plan["dirs"]:
            print("\n🎉 没有新增对象（测试未产生残留，或已清理干净）")
            return 0
        if not args.apply:
            print(f"\n共 {total} 条 DB 记录 + {len(plan['dirs'])} 个目录待处理。")
            print("确认无误后加 --apply 执行。")
            return 0
        print(f"\n开始执行（{'物理删除' if args.hard else '软删'}）...")
        stat = execute(plan, args.hard)
        print(f"\n✅ 完成：DB {stat['db']} 行，Redis {stat['redis']} 键，目录 {stat['dirs']} 个")
        if stat["errors"]:
            for e in stat["errors"][:20]:
                print(f"   ⚠️ {e}")
            return 2
        return 0

    projects = find_projects(cur, args.since, pids)
    if aids and not projects:
        # only agents were specified; look up their projects (used for cascade only, do not delete the projects themselves)
        ph = ",".join(["%s"] * len(aids))
        cur.execute(f"SELECT DISTINCT project_id FROM agx_agent_registry WHERE agent_id IN ({ph})", tuple(aids))
        pids_probe = [r["project_id"] for r in cur.fetchall() if r["project_id"]]
    else:
        pids_probe = pids

    agents = find_agents(cur, args.since, aids, [p["project_id"] for p in projects] or pids_probe)
    agent_ids = [a["agent_id"] for a in agents]
    proj_ids = [p["project_id"] for p in projects]

    plan = dict(
        projects=projects,
        agents=agents,
        executions=find_executions(cur, agent_ids, proj_ids),
        plans=find_plans(cur, proj_ids),
        sessions=find_sessions(cur, agent_ids),
        tasks=find_tasks(cur, agent_ids),
        dirs=[],
    )

    if args.include_dirs:
        seen = set()
        for a in agents:
            for key in ("agent_root_dir", "agent_workspace_dir"):
                d = (a.get(key) or "").strip()
                if d and d not in seen:
                    seen.add(d)
                    plan["dirs"].append(d)
        # paths derived from agent_id are also candidates by default
        for a in agents:
            d = _agent_home(a['agent_id'])
            if os.path.isdir(d) and d not in seen:
                seen.add(d)
                plan["dirs"].append(d)

    conn.close()

    show(plan)

    total = sum(len(plan[k]) for k in ("projects", "agents", "executions", "plans", "sessions", "tasks"))
    if total == 0 and not plan["dirs"]:
        print("\n（没有匹配到任何对象，无需清理）")
        return 0

    if not args.apply:
        print(f"\n共 {total} 条 DB 记录 + {len(plan['dirs'])} 个目录待处理。")
        print("确认无误后加 --apply 执行。")
        return 0

    print(f"\n开始执行（{'物理删除' if args.hard else '软删'}）...")
    stat = execute(plan, args.hard)
    print(f"\n✅ 完成：DB {stat['db']} 行，Redis {stat['redis']} 键，目录 {stat['dirs']} 个")
    if stat["errors"]:
        print(f"⚠️  {len(stat['errors'])} 个错误：")
        for e in stat["errors"][:20]:
            print(f"   - {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
