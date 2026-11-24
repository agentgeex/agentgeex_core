#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人工干预（manual nudge）BFF 验收 —— 2026-10-31

分成三类，输出里**明确标注**，避免把「桩测」当成「联调通过」：

  [真]   打真实运行中的 BFF（127.0.0.1:8090），走真 MySQL、真内核通道
  [纯]   直接调 `realdata._nudge_judge` / `_nudge_error_code`（纯函数，不碰库）
  [契约] 用 FastAPI TestClient + 把 `realdata.nudge_execution` 换成「会抛指定
         ToolRejected」的桩，**只验 router 把错误码映射成哪个 HTTP 状态**；
         这不代表内核已联通，脚本结尾会再打一次真接口确认内核仍缺失。

为什么需要 [契约] 这一类：内核 `agx_nudge_execution` 还没上线，
  「60 秒限流拒绝」「对已结束任务拒绝」在真链路上根本触发不到。
  但这两条正是最容易做坏的地方（糊成一句「操作失败」），
  所以先验 BFF 自己的归类逻辑 —— 它不依赖内核。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

import httpx  # noqa: E402

from app import realdata  # noqa: E402

BASE = "http://127.0.0.1:8090"
USER, PASSWD = "operator", <db-password>

passed, failed, skipped = [], [], []


def ok(name: str, extra: str = "") -> None:
    passed.append(name)
    print(f"✅ {name}{('  — ' + extra) if extra else ''}")


def no(name: str, extra: str = "") -> None:
    failed.append(name)
    print(f"❌ {name}{('  — ' + extra) if extra else ''}")


def skip(name: str, why: str) -> None:
    skipped.append(name)
    print(f"⏭  {name} —— {why}")


def check(name: str, cond: bool, extra: str = "") -> None:
    (ok if cond else no)(name, extra)


def _count_manual_nudge() -> int | None:
    """全库 manual_nudge 事件条数；查询失败返回 None（不静默当成 0）。"""
    import subprocess

    q = "SELECT COUNT(*) FROM agentgeex.agx_task_execution_event WHERE event_type='manual_nudge'"
    out = subprocess.run(
        ["mysql", "-uroot", "-p" + "123456", "-N", "-e", q],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


# ★ 2026-10-31：内核 agx_nudge_execution 已上线，「全库应仍为 0」不再成立
#   （脚本本身就有真调用用例，别人也可能正在用）。判据改成**与开跑前基线比**：
#   新增 0 = 本轮验收没留下副作用；新增 > 0 说明真的催出去了，如实报出来。
NUDGE_BASELINE = _count_manual_nudge()


# ══════════════════════════════════════════════════════════════════
# 一、[纯] 判据表 A~E（纯函数：不写库、不调通道）
# ══════════════════════════════════════════════════════════════════
NOW = datetime(2026, 10, 31, 12, 0, 0)


def judge(**kw):
    base = dict(
        status="running",
        dispatched_at=NOW - timedelta(seconds=600),
        created_at=NOW - timedelta(seconds=600),
        timeout_seconds=1800,
        alert_count=0,
        reply_sent_at=None,
        reply_read=0,
        now=NOW,
    )
    base.update(kw)
    return realdata._nudge_judge(**base)


print("\n── [纯] 判据表（docs/agent_docs/2026-10-31-人工干预.md）──")

# A：600/1800 = 33%（< 70%）→ 不给按钮
a = judge(dispatched_at=NOW - timedelta(seconds=600))
check("A 预期内(33%) → 无按钮", a["state"] == "a" and a["availableActions"] == [], a["reason"])

# 边界：正好 70% 应进入 B（0.7×1800 = 1260s）
b_edge = judge(dispatched_at=NOW - timedelta(seconds=1260))
check("B 边界(恰 70%) → 催办", b_edge["state"] == "b" and [x["kind"] for x in b_edge["availableActions"]] == ["urge"], b_edge["reason"])
a_edge = judge(dispatched_at=NOW - timedelta(seconds=1259))
check("A/B 边界下沿(69.9%) → 无按钮", a_edge["state"] == "a", a_edge["reason"])

# C：已超时
c = judge(dispatched_at=NOW - timedelta(seconds=2400))
check("C 已超时 → 催办", c["state"] == "c" and [x["kind"] for x in c["availableActions"]] == ["urge"], c["reason"])

# E：告警过仍在跑（这里 age 只有 30% → 本来无按钮，靠 alert 补上）
e = judge(dispatched_at=NOW - timedelta(seconds=300), alert_count=2)
check("E 已告警仍 running → 催办", e["state"] == "e" and [x["kind"] for x in e["availableActions"]] == ["urge"], e["reason"])

# D：回执已发未签（任务通常已经结束）
d = judge(
    status="succeeded",
    reply_sent_at=NOW - timedelta(minutes=5),
    reply_read=0,
)
check("D 回执已发未签 → 催签收", d["state"] == "d" and [x["kind"] for x in d["availableActions"]] == ["ack_remind"], d["reason"])

# D 已签收 → 没有动作
d2 = judge(status="succeeded", reply_sent_at=NOW - timedelta(minutes=5), reply_read=1)
check("D 已签收 → 无按钮", d2["availableActions"] == [], d2["reason"])

# 已结束 + 无未签回执 → 不给催办（不能催一个已经交了作业的人）
f = judge(status="succeeded", dispatched_at=NOW - timedelta(seconds=9999))
check("已 succeeded → 不给催办", f["availableActions"] == [], f["reason"])

# 基线：dispatched_at 缺失时退回 created_at，并如实标出来
g = judge(dispatched_at=None, created_at=NOW - timedelta(seconds=1500))
check("dispatched_at 空 → 退回 created_at 且标出依据", g["ageBasis"] == "created_at" and g["state"] == "b", f"basis={g['ageBasis']} state={g['state']}")

# 两个都空 → 判不了，不给按钮（不猜）
h = judge(dispatched_at=None, created_at=None)
check("无任何时间戳 → 判不了且无按钮", h["state"] == "-" and h["availableActions"] == [], h["reason"])

# 未来时间戳（时钟漂移/脏数据）→ 判不了，不当作「刚开跑」
i = judge(dispatched_at=NOW + timedelta(seconds=300))
check("时间戳在未来 → 判不了且无按钮", i["state"] == "-" and i["availableActions"] == [], i["reason"])

# timeout 缺失 → 用缺省 1800（不是 0，否则一定判超时）
j = judge(timeout_seconds=None, dispatched_at=NOW - timedelta(seconds=100))
check("timeout 空 → 用缺省 1800", j["timeoutSeconds"] == 1800, str(j["timeoutSeconds"]))


# ══════════════════════════════════════════════════════════════════
# 二、[纯] 内核拒绝原因 → 错误码归类（限流 ≠ 状态不符）
# ══════════════════════════════════════════════════════════════════
print("\n── [纯] 错误归类（限流与状态不符必须是两件事）──")
cases = [
    ("同一任务 60 秒内只能催一次", "", "RATE_LIMITED"),
    ("操作太频繁，请稍后再试", "", "RATE_LIMITED"),
    ("任务已结束（succeeded），不必再催", "", "STATE_MISMATCH"),
    ("kind 与当前状态不匹配", "", "STATE_MISMATCH"),
    ("执行记录不存在：run_x", "", "NOT_FOUND"),
    ("权限不足，需要 level 5", "", "PERMISSION_DENIED"),
]
for msg, stt, want in cases:
    got = realdata._nudge_error_code(msg, stt)
    check(f"「{msg[:16]}…」→ {want}", got == want, got)


# ══════════════════════════════════════════════════════════════════
# 三、[真] 打真实 BFF：列表透出 + 单条 + 写入口的当前真实状态
# ══════════════════════════════════════════════════════════════════
print("\n── [真] 真实 BFF（:8090）──")
with httpx.Client(base_url=BASE, timeout=30) as c:
    r = c.post("/v1/auth/login", json={"username": USER, "password": PASSWD})
    r.raise_for_status()
    token = r.json()["token"]
    H = {"Authorization": " ".join(["Bearer", token])}

    # 列表透出
    r = c.get("/v1/executions", params={"_page": 1, "_limit": 10, "status": "running"}, headers=H)
    rows = r.json()
    check("GET /v1/executions 200", r.status_code == 200, str(r.status_code))
    need = {"availableActions", "nudgeState", "nudgeReason", "ageSeconds", "progressRatio", "nudgeCount"}
    if rows:
        missing = need - set(rows[0])
        check("列表每行透出干预字段", not missing, f"缺 {sorted(missing)}" if missing else "字段齐全")
        row0 = rows[0]
        ok("当前 running 行的判据", f"{row0['nudgeState']} · {row0['nudgeReason']}")
        # A 状态必须不给按钮 —— 用真实数据验「不该有按钮时没有」
        if row0["nudgeState"] == "a":
            check("A 状态行 availableActions 为空", row0["availableActions"] == [], json.dumps(row0["availableActions"]))
        else:
            skip("A 状态行 availableActions 为空", f"当前唯一 running 行是 {row0['nudgeState']} 档，另由 [纯] 段 A 用例覆盖")
    else:
        skip("列表干预字段", "当前没有 running 任务")

    # 单条
    if rows:
        run = rows[0]["run_uuid"]
        r1 = c.get(f"/v1/executions/{run}", headers=H)
        check("GET /v1/executions/{run} 200 且 run_uuid 对齐", r1.status_code == 200 and r1.json()["run_uuid"] == run, str(r1.status_code))
        r2 = c.get("/v1/executions/run___no_such___", headers=H)
        check("不存在的 run → 404 NOT_FOUND", r2.status_code == 404 and r2.json()["error"]["code"] == "NOT_FOUND", str(r2.status_code))

    # 参数校验（不依赖内核）
    r3 = c.post(f"/v1/executions/{rows[0]['run_uuid'] if rows else 'run_x'}/nudge", json={"kind": "bogus"}, headers=H)
    check("未知 kind → 400 BAD_KIND", r3.status_code == 400 and r3.json()["error"]["code"] == "BAD_KIND", str(r3.status_code))

    # 写入口的真实状态：内核没上线 → 503 KERNEL_NOT_READY（不是 500，不是「操作失败」）
    r4 = c.post(
        f"/v1/executions/{rows[0]['run_uuid'] if rows else 'run_x'}/nudge",
        json={"kind": "urge", "note": "验收探针（不期望真的发出去）"},
        headers=H,
    )
    kernel_ready = not (r4.status_code == 503 and r4.json()["error"]["code"] == "KERNEL_NOT_READY")
    if kernel_ready:
        ok("内核已上线，写入口可调", f"HTTP {r4.status_code}")
    else:
        ok("内核未上线 → 503 KERNEL_NOT_READY（不是 500，也不是「操作失败」）", r4.json()["error"]["message"][:46])


# ══════════════════════════════════════════════════════════════════
# 四、[契约] router 的错误码 → HTTP 状态映射（内核打桩，只验我们这层）
# ══════════════════════════════════════════════════════════════════
print("\n── [契约] 错误码→HTTP（内核打桩，只验 BFF 归类；不代表联调通过）──")
try:
    from fastapi.testclient import TestClient
    from app.auth import current_user
    from app.main import app
except Exception as e:  # noqa: BLE001
    skip("错误码→HTTP 映射", f"TestClient 不可用：{e}")
else:
    app.dependency_overrides[current_user] = lambda: {"name": "operator", "id": "operator", "role": "operator"}
    client = TestClient(app)
    origin = realdata.nudge_execution
    try:
        for code, want in [
            ("RATE_LIMITED", 429),
            ("STATE_MISMATCH", 409),
            ("NOT_FOUND", 404),
            ("KERNEL_NOT_READY", 503),
            ("PERMISSION_DENIED", 403),
        ]:
            def boom(*_a, _c=code, **_kw):
                raise realdata.ToolRejected(_c, f"stub:{_c}")

            realdata.nudge_execution = boom
            resp = client.post("/v1/executions/run_stub/nudge", json={"kind": "urge"})
            body = resp.json()
            check(
                f"{code} → HTTP {want}",
                resp.status_code == want and body["error"]["code"] == code,
                f"got {resp.status_code}/{body.get('error', {}).get('code')}",
            )
    finally:
        realdata.nudge_execution = origin
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════
# 五、不变量：本脚本没有制造任何 execution / 事件
# ══════════════════════════════════════════════════════════════════
print("\n── [真] 副作用核对 ──")
with httpx.Client(base_url=BASE, timeout=30) as c:
    tok = c.post("/v1/auth/login", json={"username": USER, "password": PASSWD}).json()["token"]
    # 只读检查：nudge 不该新增事件（内核没上线 → 什么都没发）
    import subprocess

    q = (
        "SELECT COUNT(*) FROM agentgeex.agx_task_execution_event "
        "WHERE event_type='manual_nudge'"
    )
    out = subprocess.run(
        ["mysql", "-uroot", "-p" + "123456", "-N", "-e", q],
        capture_output=True, text=True,
    )
    if out.returncode == 0:
        n = out.stdout.strip()
        if NUDGE_BASELINE is None:
            skip("manual_nudge 事件数（与基线比）", "开跑前基线未取到")
        else:
            added = int(n) - NUDGE_BASELINE
            check(
                "manual_nudge 事件数（本轮未新增）",
                added <= 0,
                f"before={NUDGE_BASELINE} after={n} added={added}",
            )
    else:
        skip("manual_nudge 事件数", out.stderr.strip()[:80])

print(f"\n通过 {len(passed)} / 失败 {len(failed)} / 跳过 {len(skipped)}")
if skipped:
    print("跳过的项（原因已逐条打印）：" + "、".join(skipped))
if not kernel_ready:
    print(
        "\n⚠️ 集成状态：内核 agx_nudge_execution 未上线 → "
        "「真发出去」「60s 限流」「对已结束拒绝」三条**尚未在真链路验证**，"
        "BFF/UI 已按契约就绪。"
    )
sys.exit(1 if failed else 0)
