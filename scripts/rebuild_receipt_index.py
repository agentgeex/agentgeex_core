#!/usr/bin/env python3
"""Rebuild / verify the receipt-pending Redis index from MySQL.

The index `agx:idx:execution:receipt:pending` is a derived view: MySQL is the
source of truth. This script rebuilds it and then prints a side-by-side
comparison so drift is visible instead of guessed.

Usage:
    python scripts/rebuild_receipt_index.py            # rebuild + verify
    python scripts/rebuild_receipt_index.py --check    # verify only

Exit codes: 0 = consistent, 1 = drift detected (after rebuild), 2 = error.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agx_mcp.agx_core import (  # noqa: E402
    RECEIPT_PENDING_IDX,
    _receipt_pending_score,
    _receipt_pending_wanted,
    get_redis_client,
)
from common.mysql.mysqlservice import getRowsByFullSQl  # noqa: E402

WINDOW = 86400

SQL = f"""
SELECT task_run_uuid, run_status, needs_reply, reply_read, reply_sent_at, finished_at
FROM agx_task_execution
WHERE run_status IN ('succeeded','failed')
  AND needs_reply IN (1,2)
  AND COALESCE(reply_read,0) = 0
  AND COALESCE(reply_sent_at, finished_at) > DATE_SUB(NOW(), INTERVAL {WINDOW} SECOND)
"""


def main() -> int:
    check_only = "--check" in sys.argv
    rows = getRowsByFullSQl(SQL) or []
    want = {}
    for r in rows:
        rid = r.get("task_run_uuid")
        if not rid:
            continue
        if not _receipt_pending_wanted(
            r.get("run_status"), r.get("needs_reply"), r.get("reply_read")
        ):
            continue
        want[rid] = _receipt_pending_score(r.get("reply_sent_at"), r.get("finished_at"))

    r = get_redis_client()
    before = set(r.zrange(RECEIPT_PENDING_IDX, 0, -1))
    print(f"MySQL should hold : {len(want)}")
    print(f"Redis before      : {len(before)}")

    if not check_only:
        pipe = r.pipeline()
        pipe.delete(RECEIPT_PENDING_IDX)
        for rid, sc in want.items():
            pipe.zadd(RECEIPT_PENDING_IDX, {rid: sc})
        pipe.execute()
        print("Rebuilt.")

    after = set(r.zrange(RECEIPT_PENDING_IDX, 0, -1))
    missing = set(want) - after
    extra = after - set(want)
    print(f"Redis after       : {len(after)}")
    print(f"missing (should be in index, absent) : {len(missing)}")
    for rid in sorted(missing)[:10]:
        print(f"   - {rid}")
    print(f"extra   (in index, should not be)    : {len(extra)}")
    for rid in sorted(extra)[:10]:
        print(f"   + {rid}")

    if missing or extra:
        print("\nRESULT: FAIL (drift)")
        return 1
    print("\nRESULT: PASS (index consistent with MySQL)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(2)
