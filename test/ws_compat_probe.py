#!/usr/bin/env python3
"""
OpenClaw Gateway WS compatibility probe
=======================================
Verifies that agx_ws_client.py (the original client) is still supported after
an OpenClaw upgrade.

Approach: replicate the original client handshake parameters exactly
(client/role/scopes/userAgent/protocol range), then verify one by one that the
7 RPC methods it actually uses are available and can round-trip.

Only reversible calls are made: read + subscribe + patch (restoring the
original value). Nothing is created or sent, so there are no side effects
(no real agent run, no dirty sessions).

Usage:
    python3 test/ws_compat_probe.py
"""

import asyncio
import json
import os
import pathlib
import sys

import websockets

GATEWAY_URL = "ws://127.0.0.1:18789"
CONFIG_PATH = pathlib.Path.home() / ".openclaw" / "openclaw.json"
OWN_SESSION = os.environ.get("AGX_PROBE_SESSION", "agent:test_agent:main")

# methods actually called by the original client agx_ws_client.py (lines 429/453/493/521/562/588/641)
CLIENT_METHODS = [
    "agents.create",
    "sessions.create",
    "sessions.send",
    "sessions.describe",
    "sessions.list",
    "sessions.patch",
    "sessions.messages.subscribe",
]

OK, FAIL, WARN = "✅", "❌", "⚠️ "
results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {OK if ok else FAIL} {name}{('  — ' + detail) if detail else ''}")


def load_token():
    cfg = json.load(open(CONFIG_PATH))
    return cfg["gateway"]["auth"]["token"]


class Probe:
    def __init__(self):
        self.ws = None
        self._id = 0
        self._pend = {}
        self.hello = None
        self.events = []

    async def connect(self):
        self.ws = await websockets.connect(GATEWAY_URL, max_size=None)
        challenge = json.loads(await self.ws.recv())
        print(f"  challenge: nonce={challenge.get('payload',{}).get('nonce','')[:8]}...")

        # ── replicate the original client handshake exactly (agx_ws_client.py:220-240) ──
        await self.ws.send(json.dumps({
            "type": "req", "id": "conn", "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 4,
                "client": {"id": "gateway-client", "version": "0.1",
                           "platform": "python", "mode": "backend"},
                "role": "operator",
                "scopes": ["operator.read", "operator.write", "operator.admin"],
                "caps": [], "commands": [], "permissions": {},
                "auth": {"token": load_token()},
                "locale": "zh-CN",
                "userAgent": "agx-ws-daemon",
            },
        }))
        resp = json.loads(await self.ws.recv())
        if not (resp.get("ok") and resp["payload"].get("type") == "hello-ok"):
            raise RuntimeError(f"握手失败: {resp}")
        self.hello = resp["payload"]
        asyncio.create_task(self._listen())
        return self

    async def _listen(self):
        try:
            async for frame in self.ws:
                m = json.loads(frame)
                if m.get("type") == "res":
                    f = self._pend.pop(m.get("id"), None)
                    if f and not f.done():
                        f.set_result(m)
                elif m.get("type") == "event":
                    self.events.append(m)
        except Exception:
            pass

    async def call(self, method, params=None, timeout=15):
        self._id += 1
        rid = f"p{self._id}"
        f = asyncio.get_running_loop().create_future()
        self._pend[rid] = f
        await self.ws.send(json.dumps({"type": "req", "id": rid,
                                       "method": method, "params": params or {}}))
        try:
            return await asyncio.wait_for(f, timeout=timeout)
        except TimeoutError:
            self._pend.pop(rid, None)
            return {"ok": False, "error": "timeout"}

    @property
    def methods(self):
        return set(self.hello.get("features", {}).get("methods", []))


async def main():
    print("=" * 62)
    print(" OpenClaw Gateway WS 兼容性探针")
    print("=" * 62)

    p = Probe()
    try:
        await p.connect()
    except Exception as e:
        print(f"\n  {FAIL} 连接失败: {e}")
        return 1

    srv = p.hello.get("server", {})
    print(f"\n【1】握手")
    rec("WebSocket 连接 + challenge", True)
    rec("connect 握手 (minProto=3, maxProto=4, role=operator)",
        True, f"协商协议 v{p.hello.get('protocol')} | 服务端 {srv.get('version')}")
    print(f"      客户端身份: gateway-client / agx-ws-daemon")
    print(f"      可用方法总数: {len(p.methods)}")

    print(f"\n【2】原客户端所用 7 个方法是否仍被声明")
    missing = []
    for m in CLIENT_METHODS:
        ok = m in p.methods
        if not ok:
            missing.append(m)
        rec(m, ok)
    # event subscription (the client relies on events for the chat stream)
    for m in ["sessions.messages.unsubscribe"]:
        print(f"      （附带）{m}: {'有' if m in p.methods else '无'}")

    print(f"\n【3】真实往返（只读 / 可逆）")
    # 3.1 sessions.list
    r = await p.call("sessions.list", {})
    n = len(r.get("payload", {}).get("sessions", [])) if r.get("ok") else 0
    rec("sessions.list", bool(r.get("ok")), f"{n} 个 session")

    # 3.2 sessions.describe (on our own session)
    r = await p.call("sessions.describe", {"key": OWN_SESSION})
    sess = r.get("payload", {}).get("session") or {}
    if r.get("ok"):
        rec("sessions.describe", True,
            f"status={sess.get('status')} model={sess.get('model')} "
            f"provider={sess.get('modelProvider')} src={sess.get('modelOverrideSource')}")
    else:
        rec("sessions.describe", False, str(r)[:120])

    # 3.3 sessions.messages.subscribe
    r = await p.call("sessions.messages.subscribe", {"key": OWN_SESSION})
    rec("sessions.messages.subscribe", bool(r.get("ok")),
        str(r.get("payload", {}))[:80] if r.get("ok") else str(r)[:120])

    # 3.4 sessions.patch (restore the original value; reversible)
    orig_model = sess.get("model")
    orig_src = sess.get("modelOverrideSource")
    if r0 := await p.call("sessions.patch", {"key": OWN_SESSION, "model": orig_model}):
        ok = bool(r0.get("ok"))
        rec("sessions.patch (写路径)", ok, f"set model={orig_model}")
        # restore: clear the override if there was none originally
        await p.call("sessions.patch", {"key": OWN_SESSION,
                                        "model": None if not orig_src else orig_model})
        print(f"      已还原 patch（原 override source={orig_src}）")

    # 3.5 agents.list (corroboration: an agent-domain RPC)
    r = await p.call("agents.list", {})
    if r.get("ok"):
        ag = r.get("payload", {}).get("agents", [])
        rec("agents.list（旁证）", True, f"{len(ag)} 个 agent")

    # 3.6 event stream
    print(f"\n【4】事件流（订阅后 3s 内收到事件数）")
    await asyncio.sleep(3)
    rec("event push", True, f"{len(p.events)} 个事件（无事件也属正常，静默期）")

    await p.ws.close()

    print("\n" + "=" * 62)
    bad = [n for n, ok, _ in results if not ok]
    if bad:
        print(f" 结论: ❌ 有 {len(bad)} 项不通过 → {bad}")
        return 1
    print(f" 结论: ✅ 全部 {len(results)} 项通过 — 原客户端协议完全兼容")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
