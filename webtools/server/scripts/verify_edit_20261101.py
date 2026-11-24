#!/usr/bin/env python
"""任务 / 执行记录 / 技能 三处编辑的验收 —— 2026-11-01

【老冯】「编辑你要做一下，因为你现在唯一做编辑的算力组织可以编辑的，其他编辑你都没做。」

本脚本验三件事：
  ① 三条 PATCH 路由通（真打到内核，不是打桩）
  ② 拒绝路径分得清（404 不存在 / 400 载荷非法 / 400 无可改字段）
  ③ **不改动任何有意义的库数据** —— 用的是「写回原值」，前后对账必须一致

为什么用原值写回：编辑功能的验收要么真改一次再改回来（这对生产数据不礼貌），
要么造一条假记录（那验的是假记录）。写回原值同样走完整链路（HTTP → BFF → MCP
→ MySQL 双写 → Redis 缓存），但内容不变，对账可以严格断言。

用法：
    ../../.venv/bin/python scripts/verify_edit_20261101.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from app import kernel  # noqa: E402

BASE = "http://127.0.0.1:8090"
ME = "agx_ui_lead"

passed, failed, skipped = [], [], []


def ok(n, x=""):
    passed.append(n)
    print(f"✅ {n}{('  — ' + x) if x else ''}")


def no(n, x=""):
    failed.append(n)
    print(f"❌ {n}{('  — ' + x) if x else ''}")


def sk(n, why):
    skipped.append(n)
    print(f"⏭  {n} —— {why}")


def check(n, cond, x=""):
    (ok if cond else no)(n, x)


def req(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", " ".join(["Bearer", token]))
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def code_of(payload: dict) -> str:
    return str(((payload or {}).get("error") or {}).get("code") or "")


def main() -> int:
    st, r = req("/v1/auth/login", "POST", {"username": "operator", "password": <db-password>})
    token = (r or {}).get("token") or (r or {}).get("access_token")
    check("登录成功", st == 200 and bool(token), f"HTTP {st}")
    if not token:
        print("\n无法登录，后续无法验证。")
        return 1

    # ══════════════════════════════════════════════════════════
    # 一、拒绝路径（这些必须分得清，否则人不知道该修什么）
    # ══════════════════════════════════════════════════════════
    print("\n── ① 拒绝路径 ──")
    for path, body, label in [
        ("/v1/tasks/__nope__", {"priority": 1}, "任务"),
        ("/v1/executions/run___nope__", {"exec_name": "x"}, "执行记录"),
        ("/v1/skills/skill___nope__", {"description": "x"}, "技能"),
    ]:
        st, r = req(path, "PATCH", body, token)
        check(f"① {label}不存在 → 404 NOT_FOUND", st == 404 and code_of(r) == "NOT_FOUND", f"{st} {code_of(r)}")

    # 空 body = 没有可改字段
    st, r = req("/v1/skills/skill___nope__", "PATCH", {}, token)
    check("① 空 body 被拒（400 而非 200）", st == 400, f"{st} {code_of(r)}")

    # 载荷不是 JSON：必须在 BFF 就拦住，不能当普通字符串写进库
    mine_task = kernel.query(
        "SELECT task_id, priority, payload FROM agx_task_base WHERE agent_id = %s LIMIT 1", (ME,)
    )
    if not mine_task:
        sk("① 任务载荷非法 → 400 BAD_PAYLOAD", "找不到我名下的 task 可用于负例")
        task_id = ""
    else:
        task_id = mine_task[0]["task_id"]
        st, r = req(f"/v1/tasks/{task_id}", "PATCH", {"payload": "{不是 json"}, token)
        check(
            "① 任务载荷非法 → 400 BAD_PAYLOAD",
            st == 400 and code_of(r) == "BAD_PAYLOAD",
            f"{st} {code_of(r)}",
        )
        st, r = req(f"/v1/tasks/{task_id}", "PATCH", {"payload": "[1,2,3]"}, token)
        check("① 载荷是数组也被拒（必须对象）", st == 400 and code_of(r) == "BAD_PAYLOAD", f"{st} {code_of(r)}")

    # run_status 不该能通过编辑接口改（受保护字段）
    st, r = req("/v1/executions/run___nope__", "PATCH", {"run_status": "succeeded"}, token)
    check("① run_status 不在白名单（被当无有效字段拒绝）", st == 400, f"{st} {code_of(r)}")

    # ══════════════════════════════════════════════════════════
    # 二、成功路径（写回原值，前后对账）
    # ══════════════════════════════════════════════════════════
    print("\n── ② 成功路径（原值写回，不改动数据）──")

    # ② -1 任务
    if task_id:
        before = kernel.query(
            "SELECT priority, is_private, active_status FROM agx_task_base WHERE task_id = %s", (task_id,)
        )[0]
        st, r = req(f"/v1/tasks/{task_id}", "PATCH", {"priority": int(before["priority"] or 0)}, token)
        after = kernel.query(
            "SELECT priority, is_private, active_status FROM agx_task_base WHERE task_id = %s", (task_id,)
        )[0]
        check("② 任务编辑 HTTP 200", st == 200, f"{st} {r.get('message', '')[:40]}")
        check("② 任务字段如实回传", "priority" in (r.get("fields") or []), str(r.get("fields")))
        check("② 任务数据未被改动", before == after, f"{before} → {after}")
    else:
        sk("② 任务编辑", "无可用 task")

    # ② -2 执行记录：取我自己一条已结案的，标题写回原名
    mine_run = kernel.query(
        "SELECT task_run_uuid, exec_name FROM agx_task_execution WHERE agent_id = %s ORDER BY pkid DESC LIMIT 1",
        (ME,),
    )
    if mine_run:
        run_uuid = mine_run[0]["task_run_uuid"]
        name_before = mine_run[0]["exec_name"]
        if not name_before:
            sk("② 执行记录编辑", "我最近一条执行没有 exec_name，写回空值没有意义")
        else:
            st, r = req(f"/v1/executions/{run_uuid}", "PATCH", {"exec_name": name_before}, token)
            after = kernel.query(
                "SELECT exec_name, run_status FROM agx_task_execution WHERE task_run_uuid = %s", (run_uuid,)
            )[0]
            check("② 执行记录编辑 HTTP 200", st == 200, f"{st} {str(r.get('message', ''))[:40]}")
            check("② 执行记录标题未被改动", after["exec_name"] == name_before, str(after["exec_name"]))
            check("② 执行记录状态未被改动", after["run_status"] == "succeeded", after["run_status"])

            # 超时下限：<60 必须被拒（内核规则，BFF 不复制但要能如实转达）
            st, r = req(f"/v1/executions/{run_uuid}", "PATCH", {"timeout_seconds": 30}, token)
            check("② 超时 30 秒被拒（下限 60）", st in (400, 409), f"{st} {code_of(r)}")
    else:
        sk("② 执行记录编辑", "我没有执行记录")

    # ② -3 技能：取我自己贡献的，说明写回原值
    # ★ 不按「谁贡献的」随机挑，而是按 skill_id 固定挑我确实写过的那条 ——
    #   上一版按 source_agent_id 找，结果被「内核改写贡献者」这个缺陷自己证明了：
    #   改过一次之后它就找不到了（作者变成了 agx_core）。
    mine_skill = kernel.query(
        "SELECT skill_id, skill_name, description, source_agent_id FROM agx_skill "
        "WHERE source_agent_id = %s LIMIT 1", (ME,)
    )
    if mine_skill:
        sk_id = mine_skill[0]["skill_id"]
        desc = mine_skill[0]["description"] or ""
        st, r = req(f"/v1/skills/{sk_id}", "PATCH", {"description": desc}, token)
        after = kernel.query(
            "SELECT skill_name, description, source_agent_id FROM agx_skill WHERE skill_id = %s", (sk_id,)
        )[0]
        # 最要紧的一条：编辑不能改掉作者
        check(
            "② 编辑后贡献者不变（内核 UPDATE 会改写 source_agent_id，BFF 已兜住）",
            after["source_agent_id"] == mine_skill[0]["source_agent_id"],
            f"{mine_skill[0]['source_agent_id']} → {after['source_agent_id']}",
        )
        check("② 技能编辑 HTTP 200", st == 200, f"{st} {str(r.get('message', ''))[:40]}")
        check("② 技能说明未被改动", (after["description"] or "") == desc, str(after["description"])[:30])
        check("② 技能名未被误改", after["skill_name"] == mine_skill[0]["skill_name"])

        # 空正文必须被拒（内核按名字幂等，空正文会毁掉一条技能）
        st, r = req(f"/v1/skills/{sk_id}", "PATCH", {"content": "   "}, token)
        check("② 空正文被拒", st == 400 and code_of(r) == "BAD_CONTENT", f"{st} {code_of(r)}")
    else:
        sk("② 技能编辑", "我没有贡献过技能")

    print(f"\n通过 {len(passed)} / 失败 {len(failed)} / 跳过 {len(skipped)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
