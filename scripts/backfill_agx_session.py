#!/usr/bin/env python3
"""backfill_agx_session.py - backfill historical sessions into agx_session (idempotent, re-runnable).

Purpose
-------
After the `agx_session` table went live, only **newly created** sessions were registered
automatically. This script backfills the historical sessions already present in
`agx_task_execution` (DISTINCT session_id) in one pass, so that "look up a session name"
works over the whole history immediately.

Rules (kept consistent with the runtime ensure_session)
-----------------------------------------------------
- One distinct session_id = one session row
- run_count = number of rows for that session_id in agx_task_execution
- last_run_uuid / last_active_at = the most recent execution of that session
- agent_id / project_id / task_labs = taken from the most recent execution
- session_name = derive_session_name(...) (with a second-level fallback to task_base.description)
- Idempotent: an existing session_id only gets an empty name filled in; a manual rename is never overwritten

Usage
-----
    cd ~/repos/agentgeex
    AGX_CONFIG_PATH=agx_config.json .venv/bin/python scripts/backfill_agx_session.py            # dry run
    AGX_CONFIG_PATH=agx_config.json .venv/bin/python scripts/backfill_agx_session.py --apply    # actually write
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "mcp"))
sys.path.append(_ROOT)

from agx_core import execute_sql, derive_session_name, _session_name_hint  # noqa: E402


def main(apply: bool) -> int:
    rows = execute_sql(
        """
        SELECT e.session_id,
               COUNT(*)                    AS run_count,
               MAX(e.started_at)           AS last_active_at,
               SUBSTRING_INDEX(GROUP_CONCAT(e.task_run_uuid ORDER BY e.pkid DESC), ',', 1) AS last_run_uuid,
               SUBSTRING_INDEX(GROUP_CONCAT(e.agent_id      ORDER BY e.pkid DESC), ',', 1) AS agent_id,
               SUBSTRING_INDEX(GROUP_CONCAT(e.project_id    ORDER BY e.pkid DESC), ',', 1) AS project_id,
               SUBSTRING_INDEX(GROUP_CONCAT(e.network_id    ORDER BY e.pkid DESC), ',', 1) AS network_id,
               SUBSTRING_INDEX(GROUP_CONCAT(e.task_id       ORDER BY e.pkid DESC), ',', 1) AS task_id,
               SUBSTRING_INDEX(GROUP_CONCAT(e.send_agent_id ORDER BY e.pkid DESC), ',', 1) AS creator_agent_id
          FROM agx_task_execution e
         WHERE e.session_id IS NOT NULL AND e.session_id <> ''
         GROUP BY e.session_id
        """
    )
    print(f"[scan] 历史会话 {len(rows)} 个")

    created = updated = skipped = 0
    for r in rows:
        sk = r["session_id"]
        agent_id = r.get("agent_id") or ""
        task_id = r.get("task_id") or ""

        tl_rows = execute_sql("SELECT task_labs, description FROM agx_task_base WHERE task_id = %s", (task_id,))
        task_labs = (tl_rows[0].get("task_labs") or "").strip() if tl_rows else ""
        task_desc = (tl_rows[0].get("description") or "").strip() if tl_rows else ""

        existing = execute_sql("SELECT pkid, session_name FROM agx_session WHERE session_id = %s", (sk,))
        if existing:
            old_name = (existing[0].get("session_name") or "").strip()
            if old_name:
                skipped += 1
                continue
            if apply:
                execute_sql(
                    "UPDATE agx_session SET session_name = %s, run_count = %s, "
                    "last_run_uuid = %s, last_active_at = %s WHERE session_id = %s",
                    (derive_session_name(sk, _session_name_hint("", task_labs, task_desc), agent_id, task_labs),
                     r["run_count"], r.get("last_run_uuid"), r.get("last_active_at"), sk),
                    commit=True,
                )
            updated += 1
            continue

        name = derive_session_name(sk, _session_name_hint("", task_labs, task_desc), agent_id, task_labs)
        if apply:
            execute_sql(
                "INSERT INTO agx_session "
                "(network_id, session_id, session_name, agent_id, project_id, task_labs, creator_agent_id, "
                " run_count, last_run_uuid, last_active_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    r.get("network_id") or "", sk, name, agent_id,
                    r.get("project_id"), task_labs or None,
                    r.get("creator_agent_id"), r["run_count"],
                    r.get("last_run_uuid"), r.get("last_active_at"),
                ),
                commit=True,
            )
        created += 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] 新建 {created} / 补名 {updated} / 跳过(已有名字) {skipped}")
    if not apply:
        print("提示：加 --apply 才会真正写入。")
    return 0


if __name__ == "__main__":
    main("--apply" in sys.argv)
