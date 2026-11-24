#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人工干预 与真内核联调 —— 2026-11-01（内核 9464253 上线后补做）

2026-10-31 交付 BFF/UI 时内核还没有 `agx_nudge_execution`，三条只能 SKIP。
内核上线后补验，结论写在下面（脚本可重跑）：

  ✅ 已结束任务催办 → STATE_MISMATCH（零副作用）
  ✅ 不存在的 run → NOT_FOUND
  ✅ 未知 kind → BAD_KIND
  ✅ running 任务催办 → 200，事件落 manual_nudge、**不建 execution**、不改 run_status
  ✅ 60 秒内重复催 → RATE_LIMITED
  ⚠️ 催签收（ack_remind）在真内核里**结构性不可达** —— 见 KNOWN ISSUE

用真内核、真库。不写假桩。

用法：
    ../../.venv/bin/python scripts/verify_nudge_integration_20261101.py [--run run_xxx]

不给 --run 时自动找一条 running 任务；找不到就把「成功路径」标 SKIP 并说明原因，
绝不假装验过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from app import kernel, realdata  # noqa: E402

passed, failed, skipped, known = [], [], [], []


def ok(n, x=""):
    passed.append(n)
    print(f"✅ {n}{('  — ' + x) if x else ''}")


def no(n, x=""):
    failed.append(n)
    print(f"❌ {n}{('  — ' + x) if x else ''}")


def sk(n, why):
    skipped.append(n)
    print(f"⏭  {n} —— {why}")


def kw(n, x=""):
    known.append(n)
    print(f"⚠️  {n}{('  — ' + x) if x else ''}")


def check(n, cond, x=""):
    (ok if cond else no)(n, x)


def nudge(run: str, kind: str, note: str = ""):
    """调用真 BFF 的 realdata 层（同一份代码路径），返回 (ok, code, message, payload)。"""
    try:
        r = realdata.nudge_execution(run, kind=kind, note=note)
        return True, "", "", r
    except realdata.ToolRejected as e:
        return False, e.code, e.message, e.details


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="指定一条 running 任务作为成功路径靶子")
    args = ap.parse_args()

    print("── ① 拒绝路径（零副作用）──")
    # 已结束任务：取一条 succeeded 的
    fin = kernel.query(
        "SELECT task_run_uuid FROM agx_task_execution "
        "WHERE run_status = 'succeeded' AND network_id = 'lan_001' "
        "ORDER BY pkid DESC LIMIT 1"
    )
    if fin:
        run = fin[0]["task_run_uuid"]
        good, code, msg, _ = nudge(run, "urge", "联调：对已结束任务催办")
        check("① 已结束任务 → STATE_MISMATCH", (not good) and code == "STATE_MISMATCH", f"{code} {msg[:40]}")
    else:
        sk("① 已结束任务拒绝", "库里没有 succeeded 的记录")

    good, code, msg, _ = nudge("run___definitely_not_here___", "auto")
    check("② 不存在的 run → NOT_FOUND", (not good) and code == "NOT_FOUND", code)

    # 未知 kind：先确认有可催的对象，避免被 NOT_FOUND 抢先
    target = args.run
    if not target:
        rows = kernel.query(
            "SELECT task_run_uuid FROM agx_task_execution "
            "WHERE run_status = 'running' AND network_id = 'lan_001' "
            "ORDER BY pkid DESC LIMIT 1"
        )
        target = rows[0]["task_run_uuid"] if rows else ""
    if target:
        good, code, msg, _ = nudge(target, "bogus_kind")
        check("③ 未知 kind → BAD_KIND", (not good) and code == "BAD_KIND", code)

    print("\n── ② 成功路径（running 靶子）──")
    if not target:
        sk("④ running 任务催办成功", "当前没有 running 任务；用 --run 指定，或先派一条会跑一会儿的任务")
        sk("⑤ 60 秒限流", "依赖 ④ 先成功一次")
    else:
        st = kernel.query("SELECT run_status, needs_reply FROM agx_task_execution WHERE task_run_uuid = %s", (target,))
        status = st[0]["run_status"] if st else "?"
        if status != "running":
            sk("④ running 任务催办成功", f"指定的 {target} 当前是 {status}，不是 running")
            sk("⑤ 60 秒限流", "依赖 ④")
        else:
            task_id = kernel.query(
                "SELECT task_id FROM agx_task_execution WHERE task_run_uuid = %s", (target,)
            )[0]["task_id"]
            exec_before = kernel.query(
                "SELECT COUNT(*) c FROM agx_task_execution WHERE task_id = %s", (task_id,)
            )[0]["c"]
            ev_before = kernel.query(
                "SELECT COUNT(*) c FROM agx_task_execution_event WHERE event_type = 'manual_nudge'"
            )[0]["c"]

            # 若 60 秒内已经催过这条，首次调用必然 RATE_LIMITED ——
            # 那不是缺陷，是限流在正常工作。此时**不能**把「没法观察首次成功」
            # 记成通过，也不能记成失败，只能如实跳过并说明。
            recent = kernel.query(
                "SELECT event_id, created_at FROM agx_task_execution_event "
                "WHERE task_run_uuid = %s AND event_type = 'manual_nudge' "
                "  AND created_at >= NOW() - INTERVAL 60 SECOND LIMIT 1",
                (target,),
            )
            if recent:
                sk(
                    "④ running 任务催办成功",
                    f"60 秒窗口内已被催过（{recent[0]['event_id']} @ {recent[0]['created_at']}），观察不到首次成功",
                )
                good = False
                code = "RATE_LIMITED"
                r = {}
            else:
                good, code, msg, r = nudge(target, "urge", "联调：验证真投递")
                check("④ running 任务催办成功", good, code or str(r.get("toAgent", "")))
            if good:
                ev = kernel.query(
                    "SELECT to_agent, delivery_state, reason FROM agx_task_execution_event "
                    "WHERE task_run_uuid = %s AND event_type = 'manual_nudge' ORDER BY created_at DESC LIMIT 1",
                    (target,),
                )
                check("④ 事件已落库（manual_nudge）", bool(ev), json.dumps(ev, ensure_ascii=False, default=str)[:100])
                if ev:
                    check("④ 事件收件方 = 执行方", ev[0]["to_agent"] == kernel.query(
                        "SELECT agent_id FROM agx_task_execution WHERE task_run_uuid = %s", (target,)
                    )[0]["agent_id"], ev[0]["to_agent"])
                exec_after = kernel.query(
                    "SELECT COUNT(*) c FROM agx_task_execution WHERE task_id = %s", (task_id,)
                )[0]["c"]
                ev_after = kernel.query(
                    "SELECT COUNT(*) c FROM agx_task_execution_event WHERE event_type = 'manual_nudge'"
                )[0]["c"]
                check("④ 没有新建 execution（≠ 又派一个会超时的任务）", exec_after == exec_before, f"{exec_before} → {exec_after}")
                check("④ 只多了 1 条事件", ev_after - ev_before == 1, f"{ev_before} → {ev_after}")
                after = kernel.query(
                    "SELECT run_status, needs_reply FROM agx_task_execution WHERE task_run_uuid = %s", (target,)
                )[0]
                check("④ run_status 未被改动（受保护字段）", after["run_status"] == "running", after["run_status"])
                check("④ needs_reply 未被改动（催办不是签收）", str(after["needs_reply"]) == str(st[0]["needs_reply"]), str(after["needs_reply"]))

            good2, code2, msg2, _ = nudge(target, "urge", "联调：立刻重复催")
            check("⑤ 60 秒内重复催 → RATE_LIMITED", (not good2) and code2 == "RATE_LIMITED", f"{code2} {msg2[:36]}")

    print("\n── ③ KNOWN ISSUE：催签收在真内核里不可达 ──")
    # 有未签回执的记录（reply_sent_at 有值、reply_read=0）
    unread = kernel.query(
        "SELECT task_run_uuid, run_status, reply_read FROM agx_task_execution "
        "WHERE reply_sent_at IS NOT NULL AND reply_read = 0 AND network_id = 'lan_001' "
        "ORDER BY pkid DESC LIMIT 1"
    )
    if not unread:
        sk("⑥ 催签收路径", "当前没有「已发未签」的回执可供验证")
    else:
        run = unread[0]["task_run_uuid"]
        good, code, msg, payload = nudge(run, "ack_remind", "联调：催签收")
        if good:
            ok("⑥ 催签收可发（内核已修）", f"toAgent={payload.get('toAgent', '')}")
        else:
            kw(
                "⑥ 催签收被拒 —— 内核缺陷，不是 BFF 问题",
                f"{code}：{msg[:44]}",
            )
            # 把「为什么」说清楚：reply_sent_at 只可能出现在已结束的任务上，
            # 而内核在算 unread 之前就把 succeeded/failed/cancelled 一律挡掉了。
            dist = kernel.query(
                "SELECT run_status, COUNT(*) c FROM agx_task_execution "
                "WHERE reply_sent_at IS NOT NULL GROUP BY run_status"
            )
            print(f"   证据：有回执的任务状态分布 = {json.dumps(dist, ensure_ascii=False, default=str)}")
            print("   → 回执只在「结案」时产生，所以能催签收的任务**必然**已结束；")
            print("     而内核在 unread 判定之前就 return 了。结论：ack_remind 永远发不出去。")
            print("   修法（内核侧，一行顺序）：把「已结束」的早退移到 ack 分支之后，")
            print("     或对 kind=ack_remind 跳过该早退。")

    print(f"\n通过 {len(passed)} / 失败 {len(failed)} / 跳过 {len(skipped)} / 已知问题 {len(known)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
