"""Acceptance test for the receipt-pending Redis index (no production data touched).

Covers the five ways this index can silently go wrong:
  1. predicate drift  — index membership vs the watchdog SQL
  2. score drift      — datetime vs string vs missing timestamps
  3. partial refresh  — _build_extra_index must not move rows on partial data
  4. stale members    — reader must self-heal dirty index entries
  5. wiped index      — after agx_cache_clear the reader must rebuild, not go blind

Run: .venv/bin/python scripts/verify_receipt_index.py
Exit 0 = all assertions pass.
"""
import os, sys, datetime, time
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
sys.path.insert(0, os.path.join(os.getcwd(), "agx_mcp"))

import agx_mcp.agx_core as core

fails = []
def chk(name, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")
    if not ok: fails.append(name)

print("=== 1) _receipt_pending_wanted 判定表 ===")
W = core._receipt_pending_wanted
chk("succeeded/1/0  → 应留在索引", W("succeeded", 1, 0), True)
chk("failed/1/0     → 应留",        W("failed", 1, 0), True)
chk("succeeded/2/0  → 应留",        W("succeeded", "2", "0"), True)
chk("succeeded/3/0  → 已回，剔除",  W("succeeded", 3, 0), False)
chk("succeeded/1/1  → 已签，剔除",  W("succeeded", 1, 1), False)
chk("running/1/0    → 未完结，剔除", W("running", 1, 0), False)
chk("pending/1/0    → 剔除",        W("pending", 1, 0), False)
chk("cancelled/1/0  → 剔除",        W("cancelled", 1, 0), False)
chk("None/None/None  → 剔除",       W(None, None, None), False)
chk("succeeded/0/0  → needs_reply=0 剔除", W("succeeded", 0, 0), False)
chk("字符串数字兼容 2/'0'",          W("succeeded", "2", "0"), True)

print("\n=== 2) _receipt_pending_score ===")
S = core._receipt_pending_score
d = datetime.datetime(2026, 10, 30, 6, 48, 57)
chk("datetime", int(S(d, None)), int(d.timestamp()))
chk("字符串(库里的形态)", int(S("2026-10-30 06:48:57", None)), int(d.timestamp()))
chk("缺 reply_sent_at 用 finished_at", int(S(None, "2026-10-30 06:48:57")), int(d.timestamp()))
near = abs(S(None, None) - time.time()) < 2
print(f"  [{'PASS' if near else 'FAIL'}] 两者都缺 → 回落当前时间")
if not near: fails.append("score fallback")

print("\n=== 3) _build_extra_index 索引维护（含三字段守卫）===")
class FakePipe:
    def __init__(self): self.ops = []
    def sadd(self,*a,**k): self.ops.append(("sadd",a))
    def srem(self,*a,**k): self.ops.append(("srem",a))
    def zadd(self,key,mapping): self.ops.append(("zadd",(key,tuple(mapping.items()))))
    def zrem(self,key,member): self.ops.append(("zrem",(key,member)))
    def hset(self,*a,**k): pass
    def execute(self): return []

def index_ops(data):
    p = FakePipe()
    core._build_extra_index(p, "execution", "run_test", data)
    return [o for o in p.ops if o[0] in ("zadd","zrem") and "receipt:pending" in str(o[1])]

ops = index_ops({"run_status":"succeeded","needs_reply":"1","reply_read":"0",
                 "reply_sent_at":"2026-10-30 06:48:57"})
chk("三字段齐全+待签 → ZADD", ops[0][0] if ops else None, "zadd")
ops = index_ops({"run_status":"succeeded","needs_reply":"3","reply_read":"1"})
chk("三字段齐全+已签 → ZREM", ops[0][0] if ops else None, "zrem")
ops = index_ops({"reply_read":"1","ack_state":"read","needs_reply":"3"})
chk("部分字段(无 run_status) → 不碰索引", len(ops), 0)
ops = index_ops({"run_status":"running"})
chk("只带 run_status → 不碰索引", len(ops), 0)
ops = index_ops({"run_status":"succeeded","needs_reply":"1","reply_read":"0","alert_note":"x"})
chk("带 run_status 无 reply_sent_at → 仍 ZADD(score 回落)", ops[0][0] if ops else None, "zadd")

print("\n=== 4) 看门狗读路径：对账 + 候选筛选（用测试索引键，不碰生产）===")
import agx_watchdog as wd
TEST_KEY = "agx:idx:test:receipt:pending"
wd.RECEIPT_PENDING_IDX = TEST_KEY
core.RECEIPT_PENDING_IDX = TEST_KEY
r = wd._get_redis()
r.delete(TEST_KEY)

now = time.time()
def ts(sec_ago): return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - sec_ago))

fake_rows = [
    # should be reminded (sent 1200s ago)
    {"task_run_uuid":"run_T1","run_status":"succeeded","needs_reply":"1","reply_read":"0",
     "reply_sent_at":ts(1200),"finished_at":ts(1200),"agent_id":"agentgeex_agent","task_id":"t1",
     "send_agent_id":"a1","send_session_id":"agent:a1:main","ack_state":"sent"},
    # sent only 10s ago, below the 300s threshold
    {"task_run_uuid":"run_T2","run_status":"succeeded","needs_reply":"1","reply_read":"0",
     "reply_sent_at":ts(10),"finished_at":ts(10),"agent_id":"agentgeex_agent","task_id":"t2",
     "send_agent_id":"a1","send_session_id":"agent:a1:main","ack_state":"sent"},
    # already acknowledged -> must not be in the index
    {"task_run_uuid":"run_T3","run_status":"succeeded","needs_reply":"3","reply_read":"1",
     "reply_sent_at":ts(1200),"finished_at":ts(1200),"agent_id":"agentgeex_agent","task_id":"t3",
     "send_agent_id":"a1","send_session_id":"agent:a1:main","ack_state":"read"},
    # another agent's task -> this host must not handle it
    {"task_run_uuid":"run_T4","run_status":"succeeded","needs_reply":"1","reply_read":"0",
     "reply_sent_at":ts(2000),"finished_at":ts(2000),"agent_id":"other_agent","task_id":"t4",
     "send_agent_id":"a1","send_session_id":"agent:a1:main","ack_state":"sent"},
    # outside the 24h window
    {"task_run_uuid":"run_T5","run_status":"succeeded","needs_reply":"1","reply_read":"0",
     "reply_sent_at":ts(90000),"finished_at":ts(90000),"agent_id":"agentgeex_agent","task_id":"t5",
     "send_agent_id":"a1","send_session_id":"agent:a1:main","ack_state":"sent"},
]
import re as _re
ROWS_BY_ID = {x["task_run_uuid"]: x for x in fake_rows}

def fake_mysql(sql):
    """Faithfully emulate MySQL: SELECT * fetches one row by uuid; the reconciliation SQL filters by WHERE conditions."""
    if "FROM agx_task_execution" not in sql:
        return []
    m = _re.search(r"task_run_uuid='([^']+)'", sql)
    if m:
        row = ROWS_BY_ID.get(m.group(1))
        return [row] if row else []
    # reconciliation full scan: filter with the real WHERE semantics
    out = []
    for row in fake_rows:
        if row["run_status"] not in ("succeeded", "failed"):
            continue
        if int(row["needs_reply"]) not in (1, 2):
            continue
        if int(row["reply_read"]) == 1:
            continue
        # 24h window: decided by score
        if core._receipt_pending_score(row["reply_sent_at"], row["finished_at"]) <= now - 86400:
            continue
        out.append(row)
    return out

wd._get_mysql_rows = fake_mysql
def fake_refresh(ru):
    row = ROWS_BY_ID.get(ru)
    if row:
        r.hset(f"agx:entity:execution:{ru}", mapping={k: str(v) for k, v in row.items()})
wd._refresh_execution_cache = fake_refresh

wd._reconcile_receipt_index()
members = set(r.zrange(TEST_KEY, 0, -1))
chk("对账后索引成员（真源应含 T1/T2/T4）", sorted(members), ["run_T1", "run_T2", "run_T4"])
print("     T3 已签收不该进；T5 超 24h 窗口不该进")

cands = wd._receipt_pending_candidates({"agentgeex_agent"}, limit=20)
ids = [c["task_run_uuid"] for c in cands]
chk("候选只含本机+已过 300s", ids, ["run_T1"])

print("\n=== 5) 索引脏数据自愈：手工塞一条已签收的进索引 ===")
r.zadd(TEST_KEY, {"run_T3": now - 5000})
r.hset("agx:entity:execution:run_T3", mapping={"run_status":"succeeded","needs_reply":"3",
                                              "reply_read":"1","agent_id":"agentgeex_agent"})
cands = wd._receipt_pending_candidates({"agentgeex_agent"}, limit=20)
chk("脏成员被排除", "run_T3" in [c["task_run_uuid"] for c in cands], False)
chk("脏成员被顺手 ZREM", r.zscore(TEST_KEY, "run_T3"), None)

print("\n=== 6) 索引被清空（模拟 agx_cache_clear，标记一并清）→ 自动重建 ===")
r.delete(TEST_KEY)
r.delete(wd.RECEIPT_IDX_BUILT_KEY)
cands = wd._receipt_pending_candidates({"agentgeex_agent"}, limit=20)
chk("清空后仍能取到待催", [c["task_run_uuid"] for c in cands], ["run_T1"])

# cleanup
r.delete(TEST_KEY)
for x in fake_rows: r.delete(f"agx:entity:execution:{x['task_run_uuid']}")
print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
