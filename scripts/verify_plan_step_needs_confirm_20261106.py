#!/usr/bin/env python3
"""
verify_plan_step_needs_confirm_20261106.py - end-to-end verification of step-level needs_confirm in plans

Background (2026-11-06):
    agx_plan_deploy hard-coded needs_confirm to 0 when writing executions ->
    no step in a plan could require "human approval after execution".
    This script verifies the change of the same name against the real DB and real Redis, in four stages:

    check-deploy     after deploy: the step carries needs_confirm=1 and, with no explicit timeout, >= 1 day
    activate-fixture set plan_status to active (DB+Redis) so that plan-completion
                     detection can be observed while the step is not activated
                     (deliberately not through agx_activate_plan: that would dispatch the step to a real
                     Agent and introduce uncertainty unrelated to this change)
    check-awaiting   after finish(succeeded): the step lands in awaiting_confirm and the plan must not be
                     completed - consistent across DB / Redis / _check_plan_completion /
                     watchdog _is_plan_completed
    check-approved   after human approval: step=succeeded, plan completed
    cleanup          delete the fixture: DB rows + Redis entity keys / all index members / session rows

Usage (real DB and Redis, no mocks):
    python3 scripts/verify_plan_step_needs_confirm_20261106.py check-deploy \
        --plan-id plan_xxx --run-uuid run_xxx
"""

import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "agx_mcp"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.append(_REPO)

CONFIRM_MIN_TIMEOUT = 86400  # same value as mcp_server._CONFIRM_WAIT_TIMEOUT_DEFAULT

_fails = []


def _check(cond, label, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {label}" + (f" | {detail}" if detail else ""))
    if not cond:
        _fails.append(f"{label} | {detail}")


def _db_exec():
    from mcp_server import execute_sql  # noqa: E402

    return execute_sql


def _redis():
    from mcp_server import get_redis_client  # noqa: E402

    return get_redis_client()


def _exec_row(run_uuid):
    execute_sql = _db_exec()
    rows = execute_sql(
        "SELECT task_run_uuid, plan_id, agent_id, run_status, needs_confirm, "
        "timeout_seconds, session_id FROM agx_task_execution WHERE task_run_uuid = %s",
        (run_uuid,),
    )
    return rows[0] if rows else None


def _plan_row(plan_id):
    execute_sql = _db_exec()
    rows = execute_sql(
        "SELECT plan_id, plan_status, completed_at, host_id FROM agx_plan WHERE plan_id = %s",
        (plan_id,),
    )
    return rows[0] if rows else None


def _redis_exec_hash(run_uuid):
    r = _redis()
    return r.hgetall(f"agx:entity:execution:{run_uuid}")


def _redis_plan_hash(plan_id):
    r = _redis()
    return r.hgetall(f"agx:entity:plan:{plan_id}")


def _watchdog_verdict(plan_id):
    """The watchdog's own predicate (not a copy)."""
    from agx_watchdog import _is_plan_completed  # noqa: E402

    return _is_plan_completed(plan_id)


def _dump(label, run_uuid, plan_id):
    ex = _exec_row(run_uuid) or {}
    pl = _plan_row(plan_id) or {}
    rex = _redis_exec_hash(run_uuid)
    rpl = _redis_plan_hash(plan_id)
    print(
        json.dumps(
            {
                "label": label,
                "db_execution": {
                    "run_status": ex.get("run_status"),
                    "needs_confirm": ex.get("needs_confirm"),
                    "timeout_seconds": ex.get("timeout_seconds"),
                },
                "db_plan": {"plan_status": pl.get("plan_status"), "completed_at": pl.get("completed_at")},
                "redis_execution": {
                    "run_status": rex.get("run_status"),
                    "needs_confirm": rex.get("needs_confirm"),
                    "timeout_seconds": rex.get("timeout_seconds"),
                },
                "redis_plan": {"plan_status": rpl.get("plan_status")},
                "by_plan_idx": sorted(_redis().zrange(f"agx:idx:execution:by_plan:{plan_id}", 0, -1)),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def cmd_check_deploy(args):
    ex = _exec_row(args.run_uuid)
    _check(ex is not None, "execution 行存在", args.run_uuid)
    if not ex:
        return
    _check(
        str(ex.get("needs_confirm")) == "1",
        "DB: step.needs_confirm == 1（改动前恒为 0）",
        f"got {ex.get('needs_confirm')}",
    )
    _check(
        int(ex.get("timeout_seconds") or 0) >= CONFIRM_MIN_TIMEOUT,
        "DB: 未显式给超时时抬到 ≥1 天（_timeout_for_confirm）",
        f"timeout_seconds={ex.get('timeout_seconds')}",
    )
    rex = _redis_exec_hash(args.run_uuid)
    _check(
        rex.get("needs_confirm") == "1",
        "Redis: execution 哈希带 needs_confirm=1（看门狗 _activate_next 读这里）",
        f"got {rex.get('needs_confirm')!r}",
    )
    _check(
        str(rex.get("timeout_seconds")) == str(ex.get("timeout_seconds")),
        "Redis: timeout_seconds 与 DB 一致",
        f"redis={rex.get('timeout_seconds')} db={ex.get('timeout_seconds')}",
    )
    pl = _plan_row(args.plan_id) or {}
    _check(pl.get("plan_status") == "draft", "DB: 计划部署后为 draft", f"got {pl.get('plan_status')}")
    _dump("check-deploy", args.run_uuid, args.plan_id)


def cmd_activate_fixture(args):
    """Set plan_status to active (DB+Redis). This is fixture setup, not the logic under test."""
    execute_sql = _db_exec()
    execute_sql(
        "UPDATE agx_plan SET plan_status='active' WHERE plan_id=%s",
        (args.plan_id,),
        commit=True,
    )
    r = _redis()
    r.hset(f"agx:entity:plan:{args.plan_id}", "plan_status", "active")
    pl = _plan_row(args.plan_id) or {}
    _check(pl.get("plan_status") == "active", "fixture: plan_status 置 active（DB）", str(pl.get("plan_status")))
    _check(
        _redis_plan_hash(args.plan_id).get("plan_status") == "active",
        "fixture: plan_status 置 active（Redis）",
    )


def cmd_check_awaiting(args):
    from mcp_server import _check_plan_completion  # noqa: E402

    ex = _exec_row(args.run_uuid) or {}
    _check(
        ex.get("run_status") == "awaiting_confirm",
        "DB: finish(succeeded) 后落 awaiting_confirm",
        f"got {ex.get('run_status')}",
    )
    _check(ex.get("needs_confirm") == 1, "DB: needs_confirm 仍为 1", f"got {ex.get('needs_confirm')}")
    rex = _redis_exec_hash(args.run_uuid)
    _check(
        rex.get("run_status") == "awaiting_confirm",
        "Redis: execution.run_status=awaiting_confirm",
        f"got {rex.get('run_status')!r}",
    )

    # key: run plan-completion detection once - an awaiting-approval step must not complete the plan
    _check_plan_completion(args.plan_id)
    pl = _plan_row(args.plan_id) or {}
    _check(
        pl.get("plan_status") != "completed",
        "DB: _check_plan_completion 跑过后计划仍未 completed",
        f"got {pl.get('plan_status')}",
    )
    _check(
        _redis_plan_hash(args.plan_id).get("plan_status") != "completed",
        "Redis: 计划仍未 completed",
        f"got {_redis_plan_hash(args.plan_id).get('plan_status')!r}",
    )

    verdict = _watchdog_verdict(args.plan_id)
    _check(
        tuple(verdict) == (False, None),
        "看门狗 _is_plan_completed: (False, None) = 不算完成",
        f"got {verdict}",
    )
    _dump("check-awaiting", args.run_uuid, args.plan_id)


def cmd_check_approved(args):
    ex = _exec_row(args.run_uuid) or {}
    _check(ex.get("run_status") == "succeeded", "DB: 放行后 run_status=succeeded", f"got {ex.get('run_status')}")
    _check(ex.get("needs_confirm") == 2, "DB: needs_confirm=2（已放行）", f"got {ex.get('needs_confirm')}")
    pl = _plan_row(args.plan_id) or {}
    _check(pl.get("plan_status") == "completed", "DB: 放行后计划 completed", f"got {pl.get('plan_status')}")
    _check(bool(pl.get("completed_at")), "DB: completed_at 已写", str(pl.get("completed_at")))
    _check(
        _redis_plan_hash(args.plan_id).get("plan_status") == "completed",
        "Redis: 计划 completed",
        f"got {_redis_plan_hash(args.plan_id).get('plan_status')!r}",
    )
    verdict = _watchdog_verdict(args.plan_id)
    _check(tuple(verdict) == (True, "completed"), "看门狗 _is_plan_completed: (True,'completed')", f"got {verdict}")
    _dump("check-approved", args.run_uuid, args.plan_id)


def cmd_cleanup(args):
    execute_sql = _db_exec()
    r = _redis()
    run_uuid, plan_id = args.run_uuid, args.plan_id
    ex = _exec_row(run_uuid) or {}

    # -- Redis: entity keys + all index members --
    keys = [
        f"agx:entity:execution:{run_uuid}",
        f"agx:entity:plan:{plan_id}",
        f"agx:watchdog:wake:{run_uuid}",
        f"agx:watchdog:state:{run_uuid}",
        # notification dedup keys: one left after the watchdog sends an "awaiting approval" reminder / plan_completed
        f"agx:confirm_notified:{run_uuid}",
        f"agx:notified:plan_completed:{plan_id}",
    ]
    for k in keys:
        r.delete(k)
    for s in ("running", "succeeded", "failed", "pending", "cancelled", "draft", "awaiting_confirm", "unknown"):
        r.zrem(f"agx:idx:execution:status:{s}", run_uuid)
    r.zrem("agx:idx:execution:all", run_uuid)
    r.zrem(f"agx:idx:execution:by_plan:{plan_id}", run_uuid)
    if ex.get("task_id"):
        r.srem(f"agx:idx:execution:by_task:{ex['task_id']}", run_uuid)
    r.zrem("agx:idx:plan:all", plan_id)
    r.srem(f"agx:idx:plan:by_project:{args.project_id}", plan_id)
    if args.created_by:
        r.srem(f"agx:idx:plan:by_agent:{args.created_by}", plan_id)
    r.zrem("agx:idx:execution:receipt:pending", run_uuid)
    if ex.get("session_id"):
        r.delete(f"agx:entity:session:{ex['session_id']}")

    # -- MySQL: execution rows -> plan rows -> session rows --
    execute_sql("DELETE FROM agx_task_execution WHERE plan_id = %s", (plan_id,), commit=True)
    execute_sql("DELETE FROM agx_plan WHERE plan_id = %s", (plan_id,), commit=True)
    if ex.get("session_id"):
        execute_sql("DELETE FROM agx_session WHERE session_id = %s", (ex["session_id"],), commit=True)

    left_exec = execute_sql("SELECT COUNT(*) AS c FROM agx_task_execution WHERE plan_id = %s", (plan_id,))
    left_plan = execute_sql("SELECT COUNT(*) AS c FROM agx_plan WHERE plan_id = %s", (plan_id,))
    left_keys = [k for k in keys if r.exists(k)]
    _check(int(left_exec[0]["c"]) == 0, "cleanup: execution 行已清", f"left={left_exec[0]['c']}")
    _check(int(left_plan[0]["c"]) == 0, "cleanup: plan 行已清", f"left={left_plan[0]['c']}")
    _check(not left_keys, "cleanup: Redis 实体键已清", f"left={left_keys}")
    _check(
        not r.zrange(f"agx:idx:execution:by_plan:{plan_id}", 0, -1),
        "cleanup: by_plan 索引已清",
    )
    _check(
        r.zscore("agx:idx:plan:all", plan_id) is None,
        "cleanup: plan:all 索引已清",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=[
        "check-deploy", "activate-fixture", "check-awaiting", "check-approved", "cleanup",
    ])
    ap.add_argument("--plan-id", required=True)
    ap.add_argument("--run-uuid", required=True)
    ap.add_argument("--project-id", default="proj_b2ed70d5ac17")
    ap.add_argument("--created-by", default="agentgeex_agent")
    args = ap.parse_args()

    {
        "check-deploy": cmd_check_deploy,
        "activate-fixture": cmd_activate_fixture,
        "check-awaiting": cmd_check_awaiting,
        "check-approved": cmd_check_approved,
        "cleanup": cmd_cleanup,
    }[args.stage](args)

    if _fails:
        print("\n=== FAILED ===")
        for f in _fails:
            print(" -", f)
        sys.exit(1)
    print("\n=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
