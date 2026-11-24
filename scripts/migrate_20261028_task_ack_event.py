#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-10-28 migration: ack/alert columns on agx_task_execution + agx_task_execution_event table.

Design reference: the watchdog alert/wake/ack remediation plan under docs/agent_docs.

Idempotent: every step checks information_schema first and skips if the object already exists.
Safe: only adds columns/indexes/tables; never alters or drops existing columns or data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.mysql.mysqlservice import executeSql, getRowsByFullSQl  # noqa: E402

DB = os.environ.get("AGX_DB_NAME", "agentgeex")
TABLE = "agx_task_execution"
EVENT_TABLE = "agx_task_execution_event"

COLUMNS = [
    # (name, ddl_fragment, comment)
    ("reply_sent_at", "datetime DEFAULT NULL",
     "签条(回执)发出时刻：MCP dispatch 入队成功时写，仅 is_reply_to 非空的行有值"),
    ("reply_delivered_at", "datetime DEFAULT NULL",
     "签条投递确认时刻：daemon 收到 ws_client delivered 结果时写，仅回执行有值"),
    ("alert_level", "tinyint NOT NULL DEFAULT 0",
     "提醒等级：0无 1唤醒中 2已告警 3已升级人类 9已收口"),
    ("alert_count", "int NOT NULL DEFAULT 0",
     "已发告警次数（看门狗写入，替代 Redis alert_count）"),
    ("wake_count", "int NOT NULL DEFAULT 0",
     "已发唤醒次数（看门狗写入，按 70/80/90 百分比档位，每档一次）"),
    ("alerted_at", "datetime DEFAULT NULL",
     "最近一次提醒(唤醒或告警)发出时刻，兼作告警宽限期基准"),
    ("alert_reason", "varchar(255) DEFAULT NULL",
     "最近一次提醒原因（如：超时（已运行3600s，限制1800s））"),
    ("alert_target", "varchar(64) DEFAULT NULL",
     "最近一次提醒发给了哪个 agent（写明收件人，便于溯源）"),
    ("alert_note", "text",
     "发起方填写的影响总结/处置结论，替代原 alert execution.summary"),
    ("ack_state", "varchar(16) NOT NULL DEFAULT 'none'",
     "签收聚合状态：none/sent/delivered/read/failed（由三时间戳派生，供筛选）"),
]

INDEXES = [
    ("idx_alert_level", "(`alert_level`, `alerted_at`)"),
]

EVENT_DDL = f"""
CREATE TABLE IF NOT EXISTS `{EVENT_TABLE}` (
  `event_id`       varchar(64)  NOT NULL COMMENT '事件ID evt_xxx',
  `task_run_uuid`  varchar(64)  NOT NULL COMMENT '被关注的任务',
  `event_type`     varchar(24)  NOT NULL COMMENT 'dispatch/receipt/wake/alert/escalate/ack/revived',
  `level`          tinyint      NOT NULL DEFAULT 0 COMMENT '0提示 1警告 2严重',
  `from_agent`     varchar(64)  DEFAULT NULL COMMENT '发出方（看门狗=agx_core）',
  `to_agent`       varchar(64)  DEFAULT NULL COMMENT '接收方',
  `to_session`     varchar(128) DEFAULT NULL COMMENT '发进哪个会话',
  `channel`        varchar(16)  DEFAULT NULL COMMENT 'queue/ws/tmux',
  `reason`         varchar(255) DEFAULT NULL COMMENT '本次事件原因',
  `payload`        text COMMENT '原始信封 JSON',
  `delivery_state` varchar(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/sent/delivered/failed',
  `delivered_at`   datetime     DEFAULT NULL COMMENT '投递确认时刻',
  `ack_state`      varchar(16)  NOT NULL DEFAULT 'none' COMMENT 'none/read',
  `ack_at`         datetime     DEFAULT NULL COMMENT '签收时刻',
  `created_at`     datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件发生时刻',
  PRIMARY KEY (`event_id`),
  KEY `idx_run`  (`task_run_uuid`, `created_at`),
  KEY `idx_type` (`event_type`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务事件流：唤醒/告警/回执/签收逐条留痕'
"""


def col_exists(name: str) -> bool:
    rows = getRowsByFullSQl(
        "SELECT COUNT(*) AS c FROM information_schema.columns "
        f"WHERE table_schema='{DB}' AND table_name='{TABLE}' AND column_name='{name}'"
    )
    return bool(rows and int(rows[0]["c"]) > 0)


def idx_exists(name: str) -> bool:
    rows = getRowsByFullSQl(
        "SELECT COUNT(*) AS c FROM information_schema.statistics "
        f"WHERE table_schema='{DB}' AND table_name='{TABLE}' AND index_name='{name}'"
    )
    return bool(rows and int(rows[0]["c"]) > 0)


def table_exists(name: str) -> bool:
    rows = getRowsByFullSQl(
        "SELECT COUNT(*) AS c FROM information_schema.tables "
        f"WHERE table_schema='{DB}' AND table_name='{name}'"
    )
    return bool(rows and int(rows[0]["c"]) > 0)


def main() -> int:
    changed = 0
    for name, ddl, comment in COLUMNS:
        if col_exists(name):
            print(f"[skip] column {name} already exists")
            continue
        sql = f"ALTER TABLE `{TABLE}` ADD COLUMN `{name}` {ddl} COMMENT '{comment}'"
        executeSql(sql, commit=True)
        print(f"[add ] column {name}  ({ddl})")
        changed += 1

    for name, cols in INDEXES:
        if idx_exists(name):
            print(f"[skip] index {name} already exists")
            continue
        executeSql(f"ALTER TABLE `{TABLE}` ADD INDEX `{name}` {cols}", commit=True)
        print(f"[add ] index {name} {cols}")
        changed += 1

    if table_exists(EVENT_TABLE):
        print(f"[skip] table {EVENT_TABLE} already exists")
    else:
        executeSql(EVENT_DDL, commit=True)
        print(f"[add ] table {EVENT_TABLE}")
        changed += 1

    print(f"\nDONE. changes={changed}")
    # Recheck
    rows = getRowsByFullSQl(
        "SELECT column_name, column_type, column_default FROM information_schema.columns "
        f"WHERE table_schema='{DB}' AND table_name='{TABLE}' "
        "AND column_name IN ('reply_sent_at','reply_delivered_at','alert_level','alert_count',"
        "'wake_count','alerted_at','alert_reason','alert_target','alert_note','ack_state') "
        "ORDER BY ordinal_position"
    )
    for r in rows:
        print(f"  {r.get('column_name') or r.get('COLUMN_NAME'):22s} {r.get('column_type') or r.get('COLUMN_TYPE'):14s} default={r.get('column_default') or r.get('COLUMN_DEFAULT')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
