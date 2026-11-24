#!/usr/bin/env python
"""验收：临时调度豁免（agx_set_dispatch_exempt）—— 接口 / 权限 / 三态 / 清理

【2026-10-31 老冯】检查项三态输出（PASS / FAIL / SKIP），全跑完必须把测试数据清干净。

覆盖：
  ① 开 30 分钟            → success 且 effective_now=true
  ② 回读（BFF 同一接口）   → 豁免中 + 剩余时间合理（≈1800s）
  ③ 立刻关闭              → cleared=true，回读 → 无豁免（until 空、active=false）
  ④ 参数打架              → minutes 与 until 同时传 → 内核报错（400，原话透出）
  ⑤ 过去时刻              → until 早于现在 → 内核报错（不允许造"假过期"）
  ⑥ 权限拒绝              → 以 worker 身份（agx_ui_lead）直连 MCP → denied
                            （证明 BFF 固定 agx_core 不是装饰：同一目标、同一参数，
                             换身份就拒）
  ⑦ 清理后断言            → 未给系统留下任何新增豁免（与开跑前 baseline 比）
  ⑧ 非 worker 不许设       → 对 project_manager 设置 → 400 NOT_APPLICABLE
                            （老冯 2026-10-31：能派活的角色本来就能发消息，给豁免是空操作）
  ⑨ 非 worker 仍可清       → 对 project_manager「清除」→ 200 放行
                            （只拦设置不拦清除，否则历史脏数据永远洗不掉）
  ⑩ 快照透出               → PM 的 dispatchExemptApplicable=false（前端据此不渲染面板）

用法（必须在 webtools/server 下跑）：
    cd webtools/server && ../..//.venv/bin/python scripts/verify_dispatch_exempt_20261031.py
或：
    cd webtools/server && ./.venv/bin/python ...   # 两种 venv 路径写法都能跑，见 run 说明
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import kernel  # noqa: E402
from app import mcp_client  # noqa: E402

BFF = "http://127.0.0.1:8090"
TARGET = ""  # 空 = 自动挑一个 worker

_pass, _fail, _skip = [], [], []


def ok(name: str, info: str = "") -> None:
    _pass.append(name)
    print(f"✅ {name}" + (f"  — {info}" if info else ""))


def no(name: str, info: str = "") -> None:
    _fail.append(name)
    print(f"❌ {name}" + (f"  — {info}" if info else ""))


def skip(name: str, why: str) -> None:
    _skip.append(name)
    print(f"⏭  {name} —— {why}")


def http(method: str, path: str, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BFF}{path}", data=data, method=method)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def db_exempt_holders() -> list[str]:
    """库里此刻「生效中」的豁免持有者（口径与内核一致：until > NOW()）。"""
    rows = kernel.query(
        "SELECT agent_id FROM agx_agent_registry WHERE dispatch_exempt_until > NOW() ORDER BY agent_id"
    ) or []
    return [str(r.get("agent_id") or "") for r in rows]


def main() -> int:
    status, login = http("POST", "/v1/auth/login", body={"username": "operator", "password": "local"})
    if status != 200 or not login.get("token"):
        no("登录 BFF", f"status={status} {login}")
        return 1
    token = login["token"]

    status, snap = http("GET", "/v1/snapshot", token)
    agents = snap.get("agents") or []
    target = next(
        (
            a for a in agents
            if TARGET and a.get("id") == TARGET
        ),
        None,
    ) or next(
        (
            a for a in agents
            if a.get("role") == "worker" and a.get("deployStatus") == "deployed" and not a.get("isTemplate")
        ),
        None,
    )
    if not target:
        skip("全部检查项", "没有可用的 worker 目标")
        print("\n通过 0 / 失败 0 / 跳过 1")
        return 0
    tgt = target["id"]
    print(f"目标 worker: {tgt}（role={target.get('role')}）\n")

    # 基线：别人（或人）可能正持有合法豁免 —— 脚本只能对**自己造成的**增量负责。
    # 断言「全库 = 0」在多人协同的环境里是错的：会把别人的合法操作当成自己的失败。
    baseline = db_exempt_holders()
    if baseline:
        print(f"⚠️  开跑前已有 {len(baseline)} 个 agent 持有生效豁免（不是本脚本开的）：{', '.join(baseline)}\n")

    # ── ① 开 30 分钟 ────────────────────────────────────────────
    status, res = http("POST", f"/v1/agents/{tgt}/actions/dispatch-exempt", token, {"minutes": 30})
    if status == 200 and res.get("ok") and res.get("effectiveNow") is True:
        ok("① 开 30 分钟 → success + effective_now", f"until={res.get('until')} rest={res.get('remainingSeconds')}s")
    else:
        no("① 开 30 分钟 → success + effective_now", f"status={status} {res}")

    # ── ② 回读 ─────────────────────────────────────────────────
    status, got = http("GET", f"/v1/agents/{tgt}", token)
    active = bool(got.get("dispatchExemptActive"))
    remain = int(got.get("dispatchExemptRemainingSeconds") or 0)
    if active and 1700 <= remain <= 1900:
        ok("② 回读 → 豁免中 + 剩余合理", f"{remain}s ≈ 30 分钟")
    else:
        no("② 回读 → 豁免中 + 剩余合理", f"active={active} remain={remain}")

    # ── ③ 关闭 ─────────────────────────────────────────────────
    status, res = http("POST", f"/v1/agents/{tgt}/actions/dispatch-exempt", token, {})
    cleared = bool(res.get("cleared"))
    status2, got2 = http("GET", f"/v1/agents/{tgt}", token)
    gone = (not got2.get("dispatchExemptActive")) and not got2.get("dispatchExemptUntil")
    if status == 200 and cleared and gone:
        ok("③ 关闭 → cleared=true，回读无豁免")
    else:
        no("③ 关闭 → cleared=true，回读无豁免", f"cleared={cleared} until={got2.get('dispatchExemptUntil')!r}")

    # ── ④ 两个参数同时传 ───────────────────────────────────────
    status, res = http(
        "POST", f"/v1/agents/{tgt}/actions/dispatch-exempt", token,
        {"minutes": 30, "until": "2030-01-01 00:00:00"},
    )
    msg = ((res.get("error") or {}).get("message") or "")
    if status == 400 and ("只能给一个" in msg or "二选一" in msg):
        ok("④ minutes+until 同时传 → 内核报错（原文透出）", msg[:40])
    else:
        no("④ minutes+until 同时传 → 内核报错", f"status={status} {msg or res}")

    # ── ⑤ 过去的时刻 ───────────────────────────────────────────
    status, res = http(
        "POST", f"/v1/agents/{tgt}/actions/dispatch-exempt", token,
        {"until": "2020-01-01 00:00:00"},
    )
    msg = ((res.get("error") or {}).get("message") or "")
    if status == 400 and "必须晚于当前时间" in msg:
        ok("⑤ until 早于现在 → 内核拒绝", msg[:40])
    else:
        no("⑤ until 早于现在 → 内核拒绝", f"status={status} {msg or res}")

    # ── ⑥ 权限：换身份直连 MCP ─────────────────────────────────
    #    同一目标、同一参数，只是 caller 换成 worker —— 内核应当 denied。
    #    这同时证明「BFF 固定用 agx_core」是真的在起作用，而不是碰巧所有人都能开。
    try:
        raw = mcp_client.call_tool(
            "agx_set_dispatch_exempt",
            caller_agent_id="agx_ui_lead",
            agent_id=tgt,
            minutes=30,
            until="",
            reason="验收脚本：权限拒绝路径",
        )
        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if isinstance(parsed, dict) and parsed.get("status") == "denied":
            ok("⑥ worker 身份 → denied（BFF 的 agx_core 不是摆设）", str(parsed.get("message"))[:44])
        else:
            no("⑥ worker 身份 → denied", str(parsed)[:120])
    except Exception as e:  # noqa: BLE001
        skip("⑥ worker 身份 → denied", f"MCP 通道不可用：{e}")

    # ── ⑦ 清理 + 断言 ──────────────────────────────────────────
    http("POST", f"/v1/agents/{tgt}/actions/dispatch-exempt", token, {})
    after = db_exempt_holders()
    _status, got3 = http("GET", f"/v1/agents/{tgt}", token)
    clean_target = (not got3.get("dispatchExemptUntil")) and not got3.get("dispatchExemptActive")

    if not clean_target:
        no("⑦ 清理目标", f"until={got3.get('dispatchExemptUntil')!r} active={got3.get('dispatchExemptActive')}")
    elif after == baseline:
        foreign = [a for a in after if a != tgt]
        detail = f"目标无残余；生效豁免 {len(after)} 个，与开跑前一致"
        if foreign:
            detail += f"（他人持有：{', '.join(foreign)}）"
        ok("⑦ 清理干净：目标无残余、未给系统留下任何新增豁免", detail)
    else:
        added = [a for a in after if a not in baseline]
        no("⑦ 未给系统留下新增豁免", f"新增 {added}（baseline={baseline}）")

    # ── ⑧ 非 worker 不许设置 ───────────────────────────────────
    pm = next((a for a in agents if a.get("role") == "project_manager"), None)
    if not pm:
        skip("⑧ 非 worker 不许设置", "快照里没有 project_manager")
    else:
        st, res = http(
            "POST", f"/v1/agents/{pm['id']}/actions/dispatch-exempt", token, {"minutes": 30}
        )
        code = (res.get("error") or {}).get("code")
        msg = (res.get("error") or {}).get("message") or ""
        if st == 400 and code == "DISPATCH_EXEMPT_NOT_APPLICABLE":
            ok("⑧ 对 project_manager 设置 → 400 明确拒绝（不是静默成功）", msg[:46])
        else:
            no("⑧ 对 project_manager 设置 → 400 NOT_APPLICABLE", f"status={st} code={code}")

        # ── ⑨ 清除仍放行（清历史脏数据不能被拦）──────────────────
        st, res = http("POST", f"/v1/agents/{pm['id']}/actions/dispatch-exempt", token, {})
        if st == 200 and res.get("cleared") is True:
            ok("⑨ 对 project_manager 清除 → 放行（只拦设置，不拦清理）")
        else:
            no("⑨ 对 project_manager 清除 → 放行", f"status={st} {res}")

        # 快照里应当把「不适用」也透出来（前端/脚本共用的唯一口径）
        _st, got_pm = http("GET", f"/v1/agents/{pm['id']}", token)
        if got_pm.get("dispatchExemptApplicable") is False:
            ok("⑩ 快照对 PM 透出 dispatchExemptApplicable=false", f"role={got_pm.get('role')}")
        else:
            no("⑩ 快照对 PM 透出 dispatchExemptApplicable=false", repr(got_pm.get("dispatchExemptApplicable")))

    print(f"\n通过 {len(_pass)} / 失败 {len(_fail)} / 跳过 {len(_skip)}")
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
