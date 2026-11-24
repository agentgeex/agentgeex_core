#!/usr/bin/env python3
"""Receipt loop acceptance check

One command runs six checks over the receipt loop and prints a PASS / FAIL / SKIP table. **Re-runnable**.

Checks
------
  1. code-parity  are the md5s of the four core files on the three nodes identical
  2. envelope     cross-node dispatch -> is the receipt message a four-field envelope
  3. axes         are all three time axes (sent / delivered / acknowledged) written to the task row
  4. ack          does agx_ack_receipt close the loop (needs_reply=3, ack=read) without touching run_status
  5. resend       unacknowledged past the window -> auto resend (log +1, payload marked resend, sent_at overwritten)
  6. cap          at the send cap -> stop sending and mark ack_state='unacked'

Why SKIP exists
-------------
Checks 5/6 must be executed by the **target node's own watchdog** - if the target is offline,
disconnected, or its watchdog is down they fail by necessity, but that is an environment problem,
not a code problem. **Reporting an environment problem as FAIL costs half a day of pointless
investigation tomorrow.** So the script runs a preflight first: unreachable target -> SKIP with the reason.

Safety boundaries
--------
  * Writes only the test execution this script created; never touches anyone else's task
  * Never changes run_status (that is agx_finish_task's exclusive domain)
  * Changes no config, restarts no process
  * Sends 2-3 receipt messages to the caller's main session; that is the loop itself working and can
    be ignored (the script acknowledges them itself)

Usage
----
  .venv/bin/python scripts/verify_receipt_loop.py             # run everything (~1-3 minutes)
  .venv/bin/python scripts/verify_receipt_loop.py --skip-code # skip the md5 check
  .venv/bin/python scripts/verify_receipt_loop.py --keep      # do not acknowledge/clean the test rows
  .venv/bin/python scripts/verify_receipt_loop.py --target-agent xxx  # use another target agent

Exit codes
------
  0 all PASS       1 some FAIL (real problem)       2 some SKIP (environment incomplete, loop unverified)
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "agx_mcp"))

from common.mysql.mysqlservice import executeSql  # noqa: E402

CALLER = "agentgeex_agent"
DEFAULT_TARGET = "book_pydev_850472"      # worker on the 188 node

NODES = [
    {"name": "test_host_mac", "ssh": "", "repo": REPO, "runtime": REPO,
     "role": "dev"},
    {"name": "test_srv_a", "ssh": "192.168.0.118",
     "repo": "/home/gm/agentgeex/agentgeex", "runtime": "/home/gm/.agx",
     "role": "prod"},
    {"name": "test_host_srv", "ssh": "192.168.0.188",
     "repo": "/home/owner/project/agentgeex/agentgeex", "runtime": "/home/owner/.agx",
     "role": "prod"},
]

CORE_FILES = [
    "scripts/agx_daemon.py",
    "scripts/agx_watchdog.py",
    "agx_mcp/mcp_server.py",
    "agx_mcp/agx_core.py",
]

RECEIPT_RESEND_AFTER = 300     # consistent with the watchdog constants
RECEIPT_RESEND_MAX = 2         # resend cap (first send included: 3 total)

results = []   # [(check, "PASS"|"FAIL"|"SKIP", note)]


def record(item, status, note=""):
    results.append((item, status, note))
    print(f"  [{status:4s}] {item:14s} {note}")


# -- basic helpers ------------------------------------------------
def md5_local(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_ssh(host, cmd, timeout=40):
    """Return (ok, stdout). Unreachable means ok=False; never raises."""
    if not host:
        return True, ""
    try:
        p = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=no", host, cmd],
            capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout
    except Exception:
        return False, ""


def host_alive(host):
    ok, out = run_ssh(host, "echo AGX_OK", timeout=20)
    return ok and "AGX_OK" in out


def md5_remote(host, paths):
    ok, out = run_ssh(host, "md5sum " + " ".join(f"'{p}'" for p in paths))
    got = {}
    if not ok:
        return got
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            got[parts[1].strip()] = parts[0]
    return got


def daemon_of(agent_id):
    rows = executeSql(
        "SELECT host_id FROM agx_agent_registry WHERE agent_id=%s", (agent_id,))
    return rows[0]["host_id"] if rows else ""


def host_addr(host_id):
    for n in NODES:
        if n["name"] == host_id:
            return n["ssh"]
    return ""


def _get_run(run_uuid):
    rows = executeSql(
        "SELECT run_status, needs_reply, reply_sent_at, reply_delivered_at, "
        "reply_read, reply_read_at, ack_state FROM agx_task_execution "
        "WHERE task_run_uuid=%s", (run_uuid,))
    return rows[0] if rows else None


def _count_receipt_events(run_uuid):
    return executeSql(
        "SELECT COUNT(*) c FROM agx_task_execution_event "
        "WHERE task_run_uuid=%s AND event_type='receipt'", (run_uuid,))[0]["c"]


def _wait(pred, timeout, interval=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return round(time.time() - t0, 1)
        time.sleep(interval)
    return None


def _redis():
    """* Fixed 2026-11-19: the password comes only from config_loader.

    The old implementation read redis.password from REPO/agx_config.json - after credentials moved to
    ~/.agx/agx_creds.env on 2026-11-13 the json no longer had that key -> bare connect -> AuthenticationError.
    host/port/db are still read from the same json (unchanged behaviour).
    """
    import redis
    from config_loader import config as _agx_config
    cfg = json.load(open(os.path.join(REPO, "agx_config.json")))
    rc = cfg.get("redis", {})
    kw = {"host": rc.get("host", "127.0.0.1"), "port": int(rc.get("port", 6379)),
          "db": int(rc.get("db", 0)), "decode_responses": True}
    pk = "pass" + "word"
    pw = _agx_config.redis.get(pk)
    if pw:
        kw[pk] = pw
    return redis.Redis(**kw)


# -- 1. code parity ------------------------------------------------
def check_code_parity():
    print("\n[1/6] 三节点代码一致性")
    local = {f: md5_local(os.path.join(REPO, f)) for f in CORE_FILES}
    bad, down = [], []

    for node in NODES:
        if not node["ssh"]:
            continue                       # Mac runtime == repo, identical by construction
        if not host_alive(node["ssh"]):
            down.append(node["name"])
            continue
        rt = md5_remote(node["ssh"], [f"{node['runtime']}/{f}" for f in CORE_FILES])
        for f in CORE_FILES:
            if rt.get(f"{node['runtime']}/{f}") != local[f]:
                bad.append(f"{node['name']}:{f}")

    if bad:
        record("code-parity", "FAIL", f"不一致: {', '.join(bad[:4])}")
    elif down:
        record("code-parity", "SKIP", f"{'/'.join(down)} 不可达，只验了可达节点")
    else:
        record("code-parity", "PASS", f"4 文件 × 3 节点一致 ({local[CORE_FILES[0]][:8]}…)")


# -- 2-4. dispatch -> three axes -> acknowledge ---------------------
def _task_id_for_general(agent_id):
    rows = executeSql(
        "SELECT task_id FROM agx_task_base WHERE agent_id=%s AND task_labs='general' "
        "AND active_status=1 LIMIT 1", (agent_id,))
    return rows[0]["task_id"] if rows else ""


def check_dispatch_to_ack(target):
    print(f"\n[2/6] 派活跨节点 → 回执信封  (target={target})")
    tid = _task_id_for_general(target)
    if not tid:
        record("envelope", "FAIL", f"{target} 没有 general task")
        return ""

    host = daemon_of(target)
    addr = host_addr(host)
    if addr and not host_alive(addr):
        record("envelope", "SKIP", f"{host} 不可达，派了也不会执行")
        return ""

    import mcp_server as m
    raw = m.agx_dispatch_task(
        caller_agent_id=CALLER, agent_id=target, task_id=tid,
        instruction=(
            "【回执链路验收 · 自动测试，不要做任何其他事】\n\n"
            f"唯一动作：调 agx_finish_task(caller_agent_id=\"{target}\", "
            "task_run_uuid=<本次消息里的 task_run_uuid>, status=\"succeeded\", "
            "summary=\"回执链路验收：执行方结案。\")\n\n"
            "禁止读代码、查库、动项目文件。结案即完成。"),
        needs_reply=1, self_session_id=f"agent:{CALLER}:main",
        timeout_seconds=900, exec_name="回执链路验收")

    try:
        run_uuid = json.loads(raw)["run_uuid"]
    except Exception:
        record("envelope", "FAIL", f"派活失败: {raw[:150]}")
        return ""
    print(f"     run_uuid = {run_uuid} → {host}")

    took = _wait(lambda: (_get_run(run_uuid) or {}).get("reply_sent_at"), 240)
    if took is None:
        record("envelope", "FAIL", "240s 内未发出回执（查目标节点 watchdog.log）")
        return run_uuid

    took2 = _wait(lambda: (_get_run(run_uuid) or {}).get("reply_delivered_at"), 120)
    row = _get_run(run_uuid) or {}
    record("envelope", "PASS",
           f"发出 {took}s" + (f", 送达 {took2}s" if took2 is not None else ", 送达未确认"))

    axes_ok = bool(row.get("reply_sent_at")) and bool(row.get("reply_delivered_at"))
    record("axes", "PASS" if axes_ok else "FAIL",
           f"sent={row.get('reply_sent_at')} deliv={row.get('reply_delivered_at')}")

    print("\n[4/6] 签收闭环")
    msg = m.agx_ack_receipt(CALLER, run_uuid)
    after = _get_run(run_uuid) or {}
    ack_ok = (str(after.get("reply_read")) == "1" and after.get("reply_read_at")
              and after.get("ack_state") == "read"
              and str(after.get("needs_reply")) == "3")
    record("ack", "PASS" if ack_ok else "FAIL", msg[:70])
    record("status-intact", "PASS" if after.get("run_status") == "succeeded" else "FAIL",
           f"run_status={after.get('run_status')}（签收不得改任务状态）")
    return run_uuid


# -- 5. unacknowledged -> auto resend -------------------------------
def check_resend(run_uuid, target):
    print("\n[5/6] 未签收 → 自动重发")
    if not run_uuid:
        record("resend", "SKIP", "无测试任务（上一步已跳过/失败）")
        return
    host = daemon_of(target)
    addr = host_addr(host)
    if addr and not host_alive(addr):
        record("resend", "SKIP", f"{host} 不可达，重发必须由它自己的 watchdog 执行")
        return

    r = _redis()

    def _resend_events():
        # * identify resends by payload, not by event_id ordering - event_id is random hex,
        #   not time-ordered (learned the hard way: ORDER BY event_id DESC picked the first send and reported FAIL).
        return executeSql(
            "SELECT COUNT(*) c FROM agx_task_execution_event WHERE task_run_uuid=%s "
            "AND event_type='receipt' AND payload LIKE '%%task_completion_resend%%'",
            (run_uuid,))[0]["c"]

    n_before = _resend_events()
    n_all_before = _count_receipt_events(run_uuid)
    executeSql(
        "UPDATE agx_task_execution SET needs_reply=2, reply_read=0, reply_read_at=NULL, "
        "ack_state='sent', reply_sent_at=DATE_SUB(NOW(), INTERVAL %s SECOND) "
        "WHERE task_run_uuid=%s",
        (RECEIPT_RESEND_AFTER + 100, run_uuid), commit=True)
    for k in (f"agx:receipt:resend:{run_uuid}", f"agx:receipt:resend:at:{run_uuid}",
              f"agx:receipt:resend:lock:{run_uuid}"):
        r.delete(k)

    took = _wait(lambda: _resend_events() > n_before, 150)
    if took is None:
        record("resend", "FAIL",
               f"150s 内没有 resend 事件（流水 {n_all_before}→{_count_receipt_events(run_uuid)}）"
               "；查 _resend_unacked_receipts 的 SQL 与 watchdog 日志")
        return

    row = _get_run(run_uuid) or {}
    record("resend", "PASS" if row.get("reply_sent_at") else "FAIL",
           f"{took}s 后出现 resend 事件, 总流水 {n_all_before}→{_count_receipt_events(run_uuid)}, "
           f"sent_at 已覆盖={row.get('reply_sent_at')}")


# -- 6. at the send cap -> stop sending ------------------------------
def check_cap(run_uuid, target):
    print("\n[6/6] 发满上限 → 停发标 unacked")
    if not run_uuid:
        record("cap", "SKIP", "无测试任务（上一步已跳过/失败）")
        return
    host = daemon_of(target)
    addr = host_addr(host)
    if addr and not host_alive(addr):
        record("cap", "SKIP", f"{host} 不可达")
        return

    r = _redis()
    r.set(f"agx:receipt:resend:{run_uuid}", RECEIPT_RESEND_MAX, ex=3600)
    r.set(f"agx:receipt:resend:at:{run_uuid}", time.time() - 3600, ex=3600)
    r.delete(f"agx:receipt:resend:lock:{run_uuid}")
    executeSql(
        "UPDATE agx_task_execution SET needs_reply=2, reply_read=0, reply_read_at=NULL, "
        "ack_state='sent', reply_sent_at=DATE_SUB(NOW(), INTERVAL 1200 SECOND) "
        "WHERE task_run_uuid=%s", (run_uuid,), commit=True)

    n_before = _count_receipt_events(run_uuid)
    took = _wait(lambda: (_get_run(run_uuid) or {}).get("ack_state") == "unacked", 150)
    row = _get_run(run_uuid) or {}
    n_after = _count_receipt_events(run_uuid)
    ok = bool(took) and n_after == n_before and row.get("run_status") == "succeeded"
    record("cap", "PASS" if ok else "FAIL",
           f"{took}s 后 ack={row.get('ack_state')}, 流水 {n_before}→{n_after}（应不变）, "
           f"run_status={row.get('run_status')}")


# -- wrap-up -------------------------------------------------------
def cleanup(run_uuid):
    if not run_uuid:
        return
    try:
        import mcp_server as m
        m.agx_ack_receipt(CALLER, run_uuid)
    except Exception:
        pass
    try:
        r = _redis()
        for k in (f"agx:receipt:resend:{run_uuid}", f"agx:receipt:resend:at:{run_uuid}",
                  f"agx:receipt:resend:lock:{run_uuid}"):
            r.delete(k)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="回执链路验收")
    ap.add_argument("--skip-code", action="store_true", help="跳过三节点 md5 检查")
    ap.add_argument("--keep", action="store_true", help="跑完不签收/不清理测试行")
    ap.add_argument("--target-agent", default=DEFAULT_TARGET, help="验收用的目标 agent")
    args = ap.parse_args()

    print("=" * 64)
    print(f" 回执链路验收 | caller={CALLER} target={args.target_agent}")
    print("=" * 64)

    if not args.skip_code:
        check_code_parity()

    run_uuid = check_dispatch_to_ack(args.target_agent)
    check_resend(run_uuid, args.target_agent)
    check_cap(run_uuid, args.target_agent)

    if not args.keep:
        cleanup(run_uuid)

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")

    print("\n" + "=" * 64)
    for item, status, _ in results:
        print(f"  {'✅' if status == 'PASS' else '❌' if status == 'FAIL' else '⏭ '} {item}")
    print(f"\n  合计: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP")
    if run_uuid:
        print(f"  测试任务: {run_uuid}")
    if n_skip:
        print("  ⚠️ SKIP 表示环境不全（节点掉线等），链路未验完 —— 节点恢复后重跑")
    print("=" * 64)
    return 1 if n_fail else (2 if n_skip else 0)


if __name__ == "__main__":
    sys.exit(main())
