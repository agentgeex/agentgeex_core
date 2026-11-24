#!/usr/bin/env python3
"""A/B 对照：豁免在 / 不在 时，worker 能否过 L5 门。

用零副作用探针：`agx_quick_cmd_send` 指向一个**一定不存在**的 cmd_id。
  · 过得了 L5 门 → 进函数体 → 返回 CMD_NOT_FOUND
  · 过不了 L5 门 → 装饰器直接拒 → 返回 permission_denied
两种返回都不会投递任何消息、不建 execution、不写业务数据。

流程：清掉自己的豁免 → 探针（应被拒）→ 开回来 → 探针（应通过）。
结束时把自己恢复到「原来那条 until 或 30 分钟」。
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import mcp_client  # noqa: E402

BFF = "http://127.0.0.1:8090"
ME = "agx_ui_lead"
PROBE_CMD = "__ab_probe_never_exists_20261031__"


def http(method, path, token="", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BFF}{path}", data=data, method=method)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", " ".join(["Bearer", token]))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def probe() -> str:
    """返回 'L5_PASSED'（进了函数体）或 'L5_DENIED'（被装饰器拦）或其他原样。"""
    raw = mcp_client.call_tool("agx_quick_cmd_send", caller_agent_id=ME, cmd_id=PROBE_CMD)
    txt = raw if isinstance(raw, str) else json.dumps(raw)
    if "CMD_NOT_FOUND" in txt:
        return "L5_PASSED"
    if "permission_denied" in txt or "权限不足" in txt:
        return "L5_DENIED"
    return txt[:160]


def main() -> int:
    _s, login = http("POST", "/v1/auth/login", body={"username": "operator", "password": "local-dev"})
    token = login.get("token")
    if not token:
        print("登录失败", login)
        return 1

    _s, before = http("GET", f"/v1/agents/{ME}", token)
    orig_until = before.get("dispatchExemptUntil") or ""
    print(f"原状态：until={orig_until!r} active={before.get('dispatchExemptActive')}")

    print(f"\n[A] 带豁免时探针 → {probe()}   （期望 L5_PASSED = 进到函数体才找不到 cmd）")

    s, cleared = http("POST", f"/v1/agents/{ME}/actions/dispatch-exempt", token, {})
    print(f"\n清除自己的豁免：status={s} cleared={cleared.get('cleared')}")
    print(f"[B] 无豁免时探针 → {probe()}   （期望 L5_DENIED = 装饰器直接拦，进程都没进）")

    s, back = http(
        "POST", f"/v1/agents/{ME}/actions/dispatch-exempt", token,
        {"minutes": 30, "reason": "A/B 对照结束后恢复（脚本自动）"},
    )
    print(f"\n恢复 30 分钟：status={s} until={back.get('until')} effectiveNow={back.get('effectiveNow')}")
    print(f"[A'] 恢复后探针 → {probe()}   （期望 L5_PASSED，证明是豁免在起作用，不是探针本身变了）")

    _s, after = http("GET", f"/v1/agents/{ME}", token)
    print(f"\n最终状态：until={after.get('dispatchExemptUntil')!r} active={after.get('dispatchExemptActive')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
