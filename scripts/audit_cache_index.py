#!/usr/bin/env python3
"""Redis cache/index health check and cleanup tool.

* Division of labour with scripts/cleanup_test_artifacts.py (do not mix them up):
    cleanup_test_artifacts.py  -- cleans "business objects produced by testing"
                                  (projects / Agents / executions / sessions / directories) by time window or snapshot; business view
    audit_cache_index.py       -- cleans "cache layer and index layer consistency" by entity/index
                                  (missing entity, index ghost, reverse-index leftovers); storage view

* Why this tool exists (established by measurement 2026-10-21):
    The kernel's _invalidate_entity() originally deleted only agx:entity:{type}:{id}
    and touched no index at all -> every delete left a ghost id in the indexes.
    The worst consequence: when _check_plan_completion read hget=None (entity missing),
    the old code counted it as "completed" -> the plan was wrongly marked completed.
    (Fixed on the kernel side, see commit cc75c86; this tool cleans existing data and runs daily checks.)

** Hard rule (learned in blood):
    "No entity in Redis" != "data deleted".
    Redis is a cache; an entity may simply not be cached (evicted / never loaded), and _get_entity falls
    back to MySQL. So orphans/ghosts **must be judged against the DB**, never against Redis presence.
    Measured lesson: scanning by "no entity in Redis" once produced 390 ghosts; after checking each one
    against the DB only 21 were real - the other 369 were real data that simply was not cached.
    Deleting indexes based on those 390 would have wiped 378 real tasks off the list entirely.

Usage:
    # 1) health check (read only): per-type DB / Redis entity / full-index counts + index overview
    python3 scripts/audit_cache_index.py --scan

    # 2) orphans: entity in Redis, gone from DB (old-style scan, kept for compatibility)
    python3 scripts/audit_cache_index.py --orphans
    python3 scripts/audit_cache_index.py --orphans --yes

    # 3) index ghosts: member in the index, row gone from DB (* DB is authoritative, most used)
    python3 scripts/audit_cache_index.py --ghosts
    python3 scripts/audit_cache_index.py --ghosts --yes

    # 4) clean one object precisely (dry run by default, Redis only; --delete-db also touches the DB)
    python3 scripts/audit_cache_index.py --plan plan_xxx
    python3 scripts/audit_cache_index.py --execution run_xxx --yes
    python3 scripts/audit_cache_index.py --task task_xxx --delete-db --yes

    # 5) batch clean by keyword (dangerous, --yes required)
    python3 scripts/audit_cache_index.py --grep e2e1013 --yes

Exit codes: 0=normal, 1=findings present (scan mode only)
"""
import argparse
import os
from pathlib import Path
import sys

AGX_ROOT = os.environ.get("AGX_ROOT") or str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(AGX_ROOT, "agx_mcp"))
os.chdir(AGX_ROOT)

import mcp_server as M  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Redis connection (the single source of credentials is config_loader / the kernel; this no longer reads config itself)
# ────────────────────────────────────────────────────────────────────────────
def get_redis():
    """Reuse the kernel's Redis client.

    * Fixed 2026-11-19 (measured AuthenticationError):
      this script used to json.load("~/.agx/agx_config.json") itself to get redis.password - but after
      the credential rework of 2026-11-13 the account/password moved to ~/.agx/agx_creds.env (injected
      into the environment by config_loader), and the config file has no such field -> running it
      directly always raised AuthenticationError.
      The kernel's get_redis_client() goes through config_loader, the single credential source;
      there must not be a second place reading the password (a classic fix-one-miss-the-repo case, and
      this audit script was the one that slipped through).
    """
    return M.get_redis_client()


# ────────────────────────────────────────────────────────────────────────────
# Index inventory: all key patterns per entity type (* must be updated here when the kernel changes)
# ────────────────────────────────────────────────────────────────────────────
EXEC_STATUSES = ("running", "succeeded", "failed", "pending", "cancelled",
                 "draft", "awaiting_confirm")


def keys_for(entity_type: str, eid: str, row: dict | None) -> list[tuple[str, str]]:
    """Return [(key, kind)] - kind: hash/set/zset.

    row = the raw DB row (its foreign-key fields are needed to locate reverse indexes).
    When row is None (entity already deleted), `--orphans` mode can still search back from Redis.
    """
    row = row or {}
    out = [("agx:entity:%s:%s" % (entity_type, eid), "hash")]

    if entity_type == "project":
        out.append(("agx:idx:project:all", "zset"))
        if row.get("network_id"):
            out.append(("agx:idx:project:by_network:%s" % row["network_id"], "set"))

    elif entity_type == "host":
        out.append(("agx:idx:host:all", "zset"))
        out.append(("agx:idx:host:online", "set"))
        if row.get("network_id"):
            out.append(("agx:idx:host:by_network:%s" % row["network_id"], "set"))

    elif entity_type == "agent":
        out.append(("agx:idx:agent:all", "zset"))
        out.append(("agx:idx:agent:active", "set"))
        if row.get("network_id"):
            out.append(("agx:idx:agent:by_network:%s" % row["network_id"], "set"))
        # * reverse: tasks / plans / notices / group_members owned by this agent
        out.append(("agx:idx:task:by_agent:%s" % eid, "set"))
        out.append(("agx:idx:plan:by_agent:%s" % eid, "set"))
        out.append(("agx:idx:notice:agent:%s" % eid, "set"))
        out.append(("agx:idx:agent_group_member:by_agent:%s" % eid, "set"))

    elif entity_type == "task":
        out.append(("agx:idx:task:all", "zset"))
        out.append(("agx:idx:task:active", "set"))
        if row.get("agent_id"):
            out.append(("agx:idx:task:by_agent:%s" % row["agent_id"], "set"))
        # * reverse: executions of this task
        out.append(("agx:idx:execution:by_task:%s" % eid, "set"))

    elif entity_type == "execution":
        out.append(("agx:idx:execution:all", "zset"))
        # * 7 status indexes, all of which may need cleaning
        for s in EXEC_STATUSES:
            out.append(("agx:idx:execution:status:%s" % s, "zset"))
        if row.get("task_id"):
            out.append(("agx:idx:execution:by_task:%s" % row["task_id"], "set"))
        if row.get("parent_run_uuid"):
            out.append(("agx:idx:execution:by_parent:%s" % row["parent_run_uuid"], "set"))
        # * this is exactly the one missed today
        if row.get("plan_id"):
            out.append(("agx:idx:execution:by_plan:%s" % row["plan_id"], "zset"))

    elif entity_type == "plan":
        out.append(("agx:idx:plan:all", "zset"))
        if row.get("project_id"):
            out.append(("agx:idx:plan:by_project:%s" % row["project_id"], "set"))
        if row.get("created_by"):
            out.append(("agx:idx:plan:by_agent:%s" % row["created_by"], "set"))
        # * reverse: all execution indexes under this plan
        out.append(("agx:idx:execution:by_plan:%s" % eid, "zset"))

    elif entity_type == "notice":
        out.append(("agx:idx:notice:all", "zset"))
        if row.get("agent_id"):
            out.append(("agx:idx:notice:agent:%s" % row["agent_id"], "set"))
        if row.get("project_id"):
            out.append(("agx:idx:notice:project:%s" % row["project_id"], "set"))
        if row.get("task_id"):
            out.append(("agx:idx:notice:task:%s" % row["task_id"], "set"))
        if row.get("run_uuid"):
            out.append(("agx:idx:notice:run:%s" % row["run_uuid"], "set"))

    elif entity_type == "skill":
        out.append(("agx:idx:skill:all", "zset"))

    elif entity_type == "agent_group":
        out.append(("agx:idx:agent_group:all", "zset"))
        if row.get("network_id"):
            out.append(("agx:idx:agent_group:by_network:%s" % row["network_id"], "set"))

    elif entity_type == "agent_group_member":
        out.append(("agx:idx:agent_group_member:all", "zset"))
        if row.get("network_id"):
            out.append(("agx:idx:agent_group_member:by_network:%s" % row["network_id"], "set"))
        if row.get("group_id"):
            out.append(("agx:idx:agent_group_member:by_group:%s" % row["group_id"], "set"))
        if row.get("agent_id"):
            out.append(("agx:idx:agent_group_member:by_agent:%s" % row["agent_id"], "set"))

    return out


# ────────────────────────────────────────────────────────────────────────────
# DB table mapping
# ────────────────────────────────────────────────────────────────────────────
#   * Pitfall: the DB primary key is an int `pkid`, but the Redis entity_id is a composite key
#       `{group_id}:{agent_id}` - different domains. Looking up pkid directly always misses
#       and reports all 27 rows as "orphans" (measured 2026-10-21).
#   Hence col is None for this type and db_row() takes the composite-key branch.
TABLES = {
    "project":            ("agx_project", "project_id"),
    "host":               ("agx_host_inventory", "host_id"),
    "agent":              ("agx_agent_registry", "agent_id"),
    "task":               ("agx_task_base", "task_id"),
    "execution":          ("agx_task_execution", "task_run_uuid"),
    "plan":               ("agx_plan", "plan_id"),
    "notice":             ("agx_agent_notice", "notice_id"),
    "skill":              ("agx_skill", "skill_id"),
    "agent_group":        ("agx_agent_group", "group_id"),
    "agent_group_member": ("agx_agent_group_member", None),   # * composite key special case
}


def db_row(entity_type: str, eid: str) -> dict | None:
    """Look up the DB row by entity ID. None means it does not exist in the DB.

    * agent_group_member special case: eid looks like "group_xxx:agent_yyy",
      which must be split into (group_id, agent_id) and queried by those columns - never by pkid.
    """
    table, col = TABLES[entity_type]
    if entity_type == "agent_group_member":
        if ":" not in eid:
            return None
        gid, aid = eid.split(":", 1)
        rows = M.execute_sql(
            "SELECT * FROM agx_agent_group_member WHERE group_id=%s AND agent_id=%s",
            (gid, aid))
        return rows[0] if rows else None
    rows = M.execute_sql("SELECT * FROM %s WHERE %s=%%s" % (table, col), (eid,))
    return rows[0] if rows else None


def db_count(entity_type: str, eid: str) -> int:
    """How many rows this entity still has in the DB."""
    table, col = TABLES[entity_type]
    if entity_type == "agent_group_member":
        if ":" not in eid:
            return 0
        gid, aid = eid.split(":", 1)
        return M.execute_sql(
            "SELECT COUNT(*) c FROM agx_agent_group_member WHERE group_id=%s AND agent_id=%s",
            (gid, aid))[0]["c"]
    return M.execute_sql("SELECT COUNT(*) c FROM %s WHERE %s=%%s" % (table, col), (eid,))[0]["c"]


def db_delete(entity_type: str, eid: str) -> str:
    """Delete the DB row; return a description."""
    table, col = TABLES[entity_type]
    if entity_type == "agent_group_member":
        gid, aid = eid.split(":", 1)
        M.execute_sql(
            "DELETE FROM agx_agent_group_member WHERE group_id=%s AND agent_id=%s",
            (gid, aid), commit=True)
        return "DB: DELETE agx_agent_group_member (%s, %s)" % (gid, aid)
    M.execute_sql("DELETE FROM %s WHERE %s=%%s" % (table, col), (eid,), commit=True)
    return "DB: DELETE FROM %s WHERE %s=%s" % (table, col, eid)


# ────────────────────────────────────────────────────────────────────────────
# clean one entity (DB + all Redis keys)
# ────────────────────────────────────────────────────────────────────────────
def purge_entity(r, entity_type: str, eid: str, delete_db: bool, yes: bool) -> dict:
    table, col = TABLES[entity_type]
    row = db_row(entity_type, eid)
    in_db = row is not None

    report = {"entity": "%s:%s" % (entity_type, eid), "in_db": in_db,
              "removed_keys": [], "explain": []}

    # ── 1) DB ──
    if in_db and delete_db:
        if yes:
            report["explain"].append(db_delete(entity_type, eid))
        else:
            report["explain"].append("DB: 将删除该行 (dry-run)")
    elif in_db:
        report["explain"].append("DB: 保留（未指定 --delete-db）")
    else:
        report["explain"].append("DB: 已不存在")

    # -- 2) Redis: entity + all indexes --
    for key, kind in keys_for(entity_type, eid, row):
        try:
            if kind == "hash":
                exists = r.exists(key)
            elif kind == "zset":
                exists = bool(r.zscore(key, eid) is not None)
            else:  # set
                exists = bool(r.sismember(key, eid))
        except Exception:
            exists = False

        if not exists:
            continue

        report["removed_keys"].append("%s [%s]" % (key, kind))
        if yes:
            try:
                if kind == "hash":
                    r.delete(key)
                elif kind == "zset":
                    r.zrem(key, eid)
                    # delete the whole zset key when it becomes empty (avoid empty-key buildup)
                    if r.zcard(key) == 0:
                        r.delete(key)
                else:
                    r.srem(key, eid)
                    if r.scard(key) == 0:
                        r.delete(key)
            except Exception as e:
                report["explain"].append("⚠️ 删 %s 失败: %s" % (key, e))

    return report


# ────────────────────────────────────────────────────────────────────────────
# orphan scan: in Redis, not in DB
# ────────────────────────────────────────────────────────────────────────────
def scan_orphans(r) -> list[dict]:
    """Scan every entity under agx:entity:* and check one by one whether the DB still has it."""
    found = []
    for key in r.scan_iter(match="agx:entity:*", count=500):
        # agx:entity:<type>:<id>
        parts = key.split(":", 3)
        if len(parts) != 4:
            continue
        _, _, etype, eid = parts
        if etype not in TABLES:
            continue
        row = db_row(etype, eid)
        if row is None:
            # find which index references it still has in Redis
            refs = []
            for k, kind in keys_for(etype, eid, None):
                if kind == "zset":
                    if r.zscore(k, eid) is not None:
                        refs.append(k)
                elif kind == "set":
                    if r.sismember(k, eid):
                        refs.append(k)
            found.append({"type": etype, "id": eid, "key": key, "refs": refs})
    return found


# ────────────────────────────────────────────────────────────────────────────
# index ghost scan: member in the index, row missing in the DB
#
# * Why this must be done separately (blood lesson 2026-10-21):
#   --orphans only covers one half: "entity in Redis, not in DB".
#   The other half is unreachable that way: id in the index but the entity hash is gone.
#   That half is more dangerous - when _check_plan_completion reads hget=None,
#   the old code counted it as "completed" -> the plan was wrongly marked completed.
#
# ** Hard rule: the DB is authoritative, never "the Redis entity is absent".
#   Redis is only a cache; an absent entity may simply be uncached (evicted / never loaded),
#   and _get_entity falls back to MySQL. Deleting indexes by "no Redis entity"
#   wipes records that really exist off the list entirely.
#   Measured: this once misjudged 390 ghosts; after checking the DB, only 21 were real.
# ────────────────────────────────────────────────────────────────────────────
IDX_SPECS = None


def _idx_specs() -> list[tuple[str, str]]:
    """All index key patterns -> the entity type of their members.

    * Maintenance rule: whenever the kernel's _sync_entity() gains an index dimension, this must be
    updated to match.
    """
    global IDX_SPECS
    if IDX_SPECS is not None:
        return IDX_SPECS

    specs = []
    for t in TABLES:
        specs.append(("agx:idx:%s:all" % t, t))

    for s in ("running", "succeeded", "failed", "pending", "cancelled",
              "draft", "awaiting_confirm"):
        specs.append(("agx:idx:execution:status:%s" % s, "execution"))

    specs += [
        ("agx:idx:task:active", "task"),
        ("agx:idx:agent:active", "agent"),
        ("agx:idx:host:online", "host"),
    ]

    for pat, mt in (
        ("agx:idx:task:by_agent:*", "task"),
        ("agx:idx:plan:by_agent:*", "plan"),
        ("agx:idx:plan:by_project:*", "plan"),
        ("agx:idx:notice:agent:*", "notice"),
        ("agx:idx:notice:project:*", "notice"),
        ("agx:idx:notice:task:*", "notice"),
        ("agx:idx:notice:run:*", "notice"),
        ("agx:idx:project:by_network:*", "project"),
        ("agx:idx:agent:by_network:*", "agent"),
        ("agx:idx:host:by_network:*", "host"),
        ("agx:idx:agent_group:by_network:*", "agent_group"),
        ("agx:idx:agent_group_member:by_network:*", "agent_group_member"),
        ("agx:idx:agent_group_member:by_group:*", "agent_group_member"),
        ("agx:idx:agent_group_member:by_agent:*", "agent_group_member"),
        ("agx:idx:execution:by_task:*", "execution"),
        ("agx:idx:execution:by_parent:*", "execution"),
        ("agx:idx:execution:by_plan:*", "execution"),
    ):
        specs.append((pat, mt))

    IDX_SPECS = specs
    return specs


def scan_ghosts(r, limit_members: int = 0) -> dict:
    """Scan index members that point at rows the DB no longer has.

    Returns {entity_type: {member_id: [index key, ...]}}
    """
    collected = {}   # mtype -> {member: [keys]}
    for spec, mtype in _idx_specs():
        if "*" in spec:
            keys = list(r.scan_iter(match=spec, count=500))
        else:
            keys = [spec] if r.exists(spec) else []

        for k in keys:
            kt = r.type(k)
            if kt == "zset":
                members = r.zrange(k, 0, -1)
            elif kt == "set":
                members = list(r.smembers(k))
            else:
                continue
            for m in members:
                collected.setdefault(mtype, {}).setdefault(m, []).append(k)

    ghosts = {}
    uncached = {}
    for mtype, d in collected.items():
        members = list(d.keys())
        if limit_members:
            members = members[:limit_members]
        for m in members:
            if mtype == "agent_group_member":
                # composite key group_id:agent_id
                parts = m.split(":", 1)
                if len(parts) != 2:
                    continue
                try:
                    row = M.execute_sql(
                        "SELECT 1 AS x FROM agx_agent_group_member"
                        " WHERE group_id=%s AND agent_id=%s LIMIT 1", tuple(parts))
                except Exception:
                    continue
            else:
                if mtype not in TABLES:
                    continue
                table, col = TABLES[mtype]
                try:
                    row = M.execute_sql(
                        "SELECT 1 AS x FROM %s WHERE %s=%%s LIMIT 1" % (table, col), (m,))
                except Exception:
                    continue
            # * present in the DB -> merely uncached, the index is correct, must not be touched
            if row:
                uncached.setdefault(mtype, {})[m] = d[m]
            else:
                ghosts.setdefault(mtype, {})[m] = d[m]
    return {"ghost": ghosts, "uncached": uncached}


def purge_ghosts(r, ghosts: dict, yes: bool) -> int:
    """Remove ghost members. yes=False only prints."""
    n = 0
    for mtype, d in sorted(ghosts.items()):
        print("\n  【%s】%d 条" % (mtype, len(d)))
        for mid, idx_keys in sorted(d.items()):
            print("    %-40s → %d 处索引" % (mid, len(idx_keys)))
            for k in idx_keys[:4]:
                print("        %s" % k)
            if len(idx_keys) > 4:
                print("        ... 还有 %d 处" % (len(idx_keys) - 4))
            if yes:
                for k in idx_keys:
                    try:
                        if r.type(k) == "zset":
                            r.zrem(k, mid)
                        else:
                            r.srem(k, mid)
                    except Exception:
                        pass
                # dedicated keys (e.g. execution:by_plan:<id>) and residual entities
                for extra in ("agx:idx:execution:by_plan:%s" % mid,
                              "agx:idx:execution:by_task:%s" % mid,
                              "agx:idx:execution:by_parent:%s" % mid,
                              "agx:idx:task:by_agent:%s" % mid,
                              "agx:idx:plan:by_agent:%s" % mid,
                              "agx:idx:notice:agent:%s" % mid,
                              "agx:idx:project:by_network:%s" % mid,
                              "agx:idx:agent:by_network:%s" % mid,
                              "agx:entity:%s:%s" % (mtype, mid)):
                    try:
                        if r.exists(extra):
                            r.delete(extra)
                    except Exception:
                        pass
            n += 1
    return n


# ────────────────────────────────────────────────────────────────────────────
# scan: list current per-entity counts
# ────────────────────────────────────────────────────────────────────────────
def scan_summary(r):
    print("=" * 68)
    print("当前状态")
    print("=" * 68)
    print("%-22s %10s %10s %10s" % ("类型", "DB", "Redis实体", "全量索引"))
    print("-" * 68)
    for etype, (table, col) in TABLES.items():
        db_n = M.execute_sql("SELECT COUNT(*) c FROM %s" % table)[0]["c"]
        redis_n = sum(1 for _ in r.scan_iter(match="agx:entity:%s:*" % etype, count=500))
        idx_key = "agx:idx:%s:all" % etype
        try:
            idx_n = r.zcard(idx_key)
        except Exception:
            idx_n = "-"
        flag = ""
        if isinstance(idx_n, int) and redis_n > idx_n + 5:
            flag = "  ⚠️ Redis实体远多于索引"
        print("%-22s %10s %10s %10s%s" % (etype, db_n, redis_n, idx_n, flag))

    print("\n索引 key 总览:")
    for k in sorted(r.scan_iter(match="agx:idx:*", count=1000)):
        t = r.type(k)
        if t == "zset":
            n = r.zcard(k)
        elif t == "set":
            n = r.scard(k)
        else:
            n = "-"
        if n == 0:
            print("  ⚠️ %-52s [%s] 空Key" % (k, t))
        else:
            print("  %-54s [%s] %s" % (k, t, n))


# ────────────────────────────────────────────────────────────────────────────
# batch lookup by keyword
# ────────────────────────────────────────────────────────────────────────────
def grep_entities(r, pattern: str) -> list[tuple[str, str]]:
    """Fuzzy-match IDs in the DB; return [(type, id)]."""
    hits = []
    q = "%%%s%%" % pattern
    probes = [
        ("plan", "agx_plan", "plan_id"),
        ("execution", "agx_task_execution", "task_run_uuid"),
        ("agent", "agx_agent_registry", "agent_id"),
        ("project", "agx_project", "project_id"),
        ("task", "agx_task_base", "task_id"),
    ]
    for etype, table, col in probes:
        rows = M.execute_sql("SELECT %s AS x FROM %s WHERE %s LIKE %%s" % (col, table, col), (q,))
        for x in rows:
            hits.append((etype, x["x"]))

    # also look in Redis (may be an orphan)
    for key in r.scan_iter(match="*%s*" % pattern, count=500):
        if key.startswith("agx:entity:"):
            parts = key.split(":", 3)
            if len(parts) == 4 and parts[2] in TABLES:
                pair = (parts[2], parts[3])
                if pair not in hits:
                    hits.append(pair)
    return hits


# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="AgentGeeX 测试数据清理工具（DB + Redis 全清）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--scan", action="store_true", help="扫描现状（只读）")
    ap.add_argument("--orphans", action="store_true", help="扫描孤儿：Redis 有实体、DB 没有")
    ap.add_argument("--ghosts", action="store_true",
                    help="扫描索引幽灵：索引里有成员、DB 没有（以 DB 为准；配合 --yes 摘除）")
    ap.add_argument("--plan", action="append", default=[], metavar="ID")
    ap.add_argument("--execution", action="append", default=[], metavar="ID")
    ap.add_argument("--agent", action="append", default=[], metavar="ID")
    ap.add_argument("--project", action="append", default=[], metavar="ID")
    ap.add_argument("--task", action="append", default=[], metavar="ID")
    ap.add_argument("--grep", metavar="KEYWORD", help="按关键词批量匹配（危险）")
    ap.add_argument("--delete-db", action="store_true",
                    help="同时删 DB 行（默认只清 Redis）")
    ap.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")
    args = ap.parse_args()

    r = get_redis()
    mode = "🔴 真删" if args.yes else "🟢 演练（dry-run，加 --yes 才真删）"
    print("模式:", mode)
    print("Redis DB:", 0, "| 端点:", r.connection_pool.connection_kwargs.get("host"))
    print()

    targets: list[tuple[str, str]] = []

    _acts = [args.orphans, args.ghosts, args.grep, args.plan, args.execution,
             args.agent, args.project, args.task]
    if args.scan or not any(_acts):
        scan_summary(r)
        if not any(_acts):
            print("\n提示: --orphans 查孤儿 / --ghosts 查索引幽灵 / --plan ID 精确清 / --grep KW 批量清")
            return

    if args.ghosts:
        print("=" * 68)
        print("索引幽灵扫描（索引有成员、DB 无此行）")
        print("★ 以 DB 为准 —— Redis 无实体 ≠ 数据已删除（可能只是没缓存）")
        print("=" * 68)
        res = scan_ghosts(r)
        g, u = res["ghost"], res["uncached"]

        tot_g = sum(len(v) for v in g.values())
        tot_u = sum(len(v) for v in u.values())

        if not tot_g:
            print("\n  ✅ 索引干净，无幽灵成员")
        else:
            print("\n  发现幽灵 %d 条：" % tot_g)
            for mt, d in sorted(g.items()):
                print("    %-22s %d" % (mt, len(d)))

        if tot_u:
            print("\n  另有 %d 条「实体未缓存但 DB 存在」→ 索引是对的，不动：" % tot_u)
            for mt, d in sorted(u.items()):
                print("    %-22s %d" % (mt, len(d)))

        if tot_g and args.yes:
            print("\n" + "=" * 68)
            print("摘除幽灵")
            print("=" * 68)
            n = purge_ghosts(r, g, yes=True)
            print("\n  ✅ 已处理 %d 条幽灵" % n)
        elif tot_g:
            print("\n  🟢 演练模式：加 --yes 真删")
            print("  预览：")
            purge_ghosts(r, g, yes=False)
        return 0

    if args.orphans:
        print("=" * 68)
        print("孤儿扫描（Redis 有实体、DB 已无）")
        print("=" * 68)
        orphans = scan_orphans(r)
        if not orphans:
            print("  ✅ 无孤儿")
        for o in orphans:
            print("  %s:%s" % (o["type"], o["id"]))
            print("    实体 key : %s" % o["key"])
            for ref in o["refs"]:
                print("    残留索引 : %s" % ref)
        if orphans and args.yes:
            print("\n执行清理...")
            for o in orphans:
                rep = purge_entity(r, o["type"], o["id"], delete_db=False, yes=True)
                print("  ✅ 已清 %s（%d 个 key）" % (rep["entity"], len(rep["removed_keys"])))
        print()

    for t, vals in (("plan", args.plan), ("execution", args.execution),
                    ("agent", args.agent), ("project", args.project),
                    ("task", args.task)):
        for v in vals:
            targets.append((t, v))

    if args.grep:
        print("=" * 68)
        print("关键词匹配: %r" % args.grep)
        print("=" * 68)
        hits = grep_entities(r, args.grep)
        for t, i in hits:
            targets.append((t, i))
        print("  命中 %d 项" % len(hits))
        print()

    if targets:
        print("=" * 68)
        print("处理清单（%d 项）" % len(targets))
        print("=" * 68)
        for t, i in targets:
            rep = purge_entity(r, t, i, delete_db=args.delete_db, yes=args.yes)
            print("\n  【%s】%s" % (t, i))
            print("    DB 存在: %s" % rep["in_db"])
            for e in rep["explain"]:
                print("    %s" % e)
            if rep["removed_keys"]:
                print("    Redis key（%d 个）:" % len(rep["removed_keys"]))
                for k in rep["removed_keys"]:
                    print("      - %s" % k)
            else:
                print("    Redis: 无残留 key")

        print("\n" + "=" * 68)
        print("复核")
        print("=" * 68)
        for t, i in targets:
            db_n = db_count(t, i)
            left = []
            for k, kind in keys_for(t, i, None):
                try:
                    if kind == "hash" and r.exists(k):
                        left.append(k)
                    elif kind == "zset" and r.zscore(k, i) is not None:
                        left.append(k)
                    elif kind == "set" and r.sismember(k, i):
                        left.append(k)
                except Exception:
                    pass
            status = "✅ 干净" if (db_n == 0 and not left) else "⚠️ 仍有残留"
            print("  %-12s %-40s DB=%d Redis残留=%d %s"
                  % (t, i, db_n, len(left), status))
            for k in left:
                print("       残留: %s" % k)


if __name__ == "__main__":
    main()
