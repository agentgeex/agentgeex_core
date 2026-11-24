#!/usr/bin/env python3
"""backfill_plan_notified_at.py - one-off backfill of the plan_completed dedup state into the DB (idempotent)

Background (2026-11-11)
------------------
The "already notified" state of plan_completed used to live only in the Redis key
`agx:notified:plan_completed:{plan_id}`. Losing that key equals "never notified",
which has already caused a full re-send three times:

  . 2026-10-22 23:3x  cache_rebuild wiped all ao:*            -> 146 re-sends
  . 2026-10-25 00:16  install.py scan_iter("agx:*") wiped all -> re-sends
  . 2026-11-11 09:26  kernel rename ao:->agx:, dedup keys stayed in the old namespace -> 182 plans re-sent 123 messages

The new logic treats `agx_plan.notified_at` (MySQL) as the single source of truth. **This script must
finish before MCP / watchdog restart**, otherwise the new code sees notified_at all NULL and treats every
existing plan as "not notified", immediately recreating the storm.

Backfill rules (three, unioned; only NULLs are filled, existing values are never overwritten)
------------------------------------------------------------------
A. A Redis dedup key exists (agx: or ao: namespace) -> notified_at = COALESCE(completed_at, created_at)
   -- the strongest evidence of "was notified": if the key is still there, it was sent.
B. plan_status in (completed, failed) and completed_at < NOW() - 3 DAY
   -> notified_at = COALESCE(completed_at, created_at)
   -- a plan that ended three days ago is long past the notification window; treat it as notified.
C. plan_status in (completed, failed) and completed_at IS NULL (historical dirty rows)
   -> notified_at = created_at
   -- without this those rows stay NULL forever and are treated as newly completed on every index rebuild.

cancelled / archived are deliberately skipped: they never notify (_is_plan_completed short-circuits),
so leaving notified_at NULL is the correct semantics, not a missed fill.

Usage
----
    cd ~/repos/agentgeex
    .venv/bin/python scripts/backfill_plan_notified_at.py            # statistics only (dry run)
    .venv/bin/python scripts/backfill_plan_notified_at.py --apply    # actually write
    .venv/bin/python scripts/backfill_plan_notified_at.py --verify   # inspect the results only
"""

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "agx_mcp"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.append(_REPO)

BATCH = 200


def _sql():
    from agx_core import execute_sql, execute_sql_rowcount

    return execute_sql, execute_sql_rowcount


def _redis():
    from agx_core import get_redis_client

    return get_redis_client()


def _scan_notified_keys(r) -> set:
    """Scan the dedup keys of both namespaces. ao: is the pre-rename prefix and may still hold leftovers."""
    keys = set()
    for pattern in ("agx:notified:plan_completed:*", "ao:notified:plan_completed:*"):
        for k in r.scan_iter(pattern, count=1000):
            keys.add(k.split("notified:plan_completed:", 1)[1])
    return keys


def _fetch_plans(execute_sql) -> list:
    return execute_sql(
        """
        SELECT plan_id, plan_status, completed_at, created_at, notified_at,
               TIMESTAMPDIFF(DAY, COALESCE(completed_at, created_at), NOW()) AS age_days
        FROM agx_plan
        ORDER BY pkid
        """
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 agx_plan.notified_at（幂等）")
    ap.add_argument("--apply", action="store_true", help="真正写入；不加只统计")
    ap.add_argument("--verify", action="store_true", help="只核对结果，不写")
    args = ap.parse_args()

    execute_sql, execute_sql_rowcount = _sql()
    r = _redis()

    rows = _fetch_plans(execute_sql)
    notified_keys = _scan_notified_keys(r)
    print(f"agx_plan 总行数: {len(rows)}")
    print(f"Redis 去重键: {len(notified_keys)} 个（agx:+ao: 两命名空间）")

    plan_by_status = {}
    for x in rows:
        plan_by_status[x["plan_status"]] = plan_by_status.get(x["plan_status"], 0) + 1
    print("plan_status 分布:", plan_by_status)

    if args.verify:
        pend = [x for x in rows if x["plan_status"] in ("completed", "failed")
                and not x["notified_at"]]
        done = [x for x in rows if x["plan_status"] in ("completed", "failed")
                and x["notified_at"]]
        print(f"\ncompleted+failed 已回填: {len(done)}")
        print(f"completed+failed 仍 NULL: {len(pend)}")
        for x in pend[:10]:
            print("  待填:", x["plan_id"], x["plan_status"], x["completed_at"])
        null_completed = [x for x in done if not x["notified_at"]]
        print("回填后仍为空:", len(null_completed))
        return 0 if not pend else 1

    todo = {}   # plan_id -> (notified_at value, rule that produced it)
    for x in rows:
        if x["notified_at"]:
            continue                      # already has a value; never overwrite
        st = x["plan_status"]
        if st not in ("completed", "failed"):
            continue                      # draft/activated/cancelled/archived must not be filled
        pid = x["plan_id"]
        base = x["completed_at"] or x["created_at"]
        if pid in notified_keys:
            todo[pid] = (base, "A:已有Redis键")
        elif x["completed_at"] and (x["age_days"] or 0) >= 3:
            todo[pid] = (x["completed_at"], "B:完成超3天")
        elif not x["completed_at"]:
            todo[pid] = (base, "C:completed_at为空")

    stat = {}
    for _, rule in todo.values():
        stat[rule] = stat.get(rule, 0) + 1
    print(f"\n待回填 {len(todo)} 行:", stat)

    # cross-check: which completed+failed rows are still not backfilled
    left = [
        x["plan_id"] for x in rows
        if x["plan_status"] in ("completed", "failed") and not x["notified_at"]
        and x["plan_id"] not in todo
    ]
    if left:
        print(f"⚠️ 不回填但仍是 completed/failed 的 {len(left)} 行（3 天内完成且无 Redis 键，"
              f"属「真未通知」，留给新逻辑通知）: {left[:5]}")

    if not args.apply:
        print("\n[dry-run] 未写入。加 --apply 执行。")
        return 0

    done = 0
    items = list(todo.items())
    for i in range(0, len(items), BATCH):
        chunk = items[i : i + BATCH]
        for pid, (when, _rule) in chunk:
            # COALESCE is a second safety net: even if the value resolves to NULL nothing is corrupted; AND notified_at IS NULL keeps it idempotent
            aff = execute_sql_rowcount(
                "UPDATE agx_plan SET notified_at = COALESCE(%s, created_at) "
                "WHERE plan_id = %s AND notified_at IS NULL",
                (when, pid),
            )
            done += aff or 0
        print(f"  批次 {i // BATCH + 1}: 累计写入 {done} 行")

    print(f"\n回填完成: {done} 行")

    # final self-check: no NULLs should remain in completed/failed (except genuinely un-notified ones from the last 3 days)
    rest = execute_sql(
        "SELECT COUNT(*) AS n FROM agx_plan "
        "WHERE plan_status IN ('completed','failed') AND notified_at IS NULL"
    )
    n = rest[0]["n"] if rest else -1
    print(f"自检 completed+failed 仍 NULL: {n} 行")
    return 0 if n == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
