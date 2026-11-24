"""Quick entries -- attached directly to the `is_quick` column on the 3 core tables, **bypassing MCP**.

[2026-11-13 22:03 Feng] first request:
    "I do many operations repeatedly but have to hunt for them in a list every time (e.g. send message;
     close it and it is gone). Could we add a module called Quick Entries: a session / an agent / a project
     that I mark as quick, and when I open this window they are right there."

[2026-11-13 22:25 Feng changed the definition -- this document was rewritten accordingly]
    "That table (agx_quick_entry) is useless; we still need to add columns. I will tell you which core
     tables: first sessions, second agents, third projects." (22:31 added: "the plan table is needed too")
     "Drop that table I just made... if we ever need it we would have to add an index anyway, no point.
      Just add columns in the tables."

    => the standalone relation table is dropped, replaced by:
        agx_session.is_quick / quick_order
        agx_agent_registry.is_quick / quick_order
        agx_project.is_quick / quick_order

★ Semantic change (important, do not read it the old way):
    in the standalone-table era it was "who favourited what" (owner_agent_id, one set per person);
    in the column era it is "is this record itself quick" -- **one global set**, whoever marks it, everyone sees it.
    Feng explicitly chose this definition at 22:25 ("just add columns in the tables"), no per-person buckets.

★ Why writing these core tables goes direct from the BFF (`kernel.execute`) rather than through MCP:
    1. Feng 22:22: "maintain this inside the BFF... do not build it into MCP"
    2. The kernel (watchdog/scheduling) has nothing to do with "whether the UI pins it to the home page";
       adding an entity/tool/permission would be spinning in place
    3. `is_quick` is a pure display marker; getting it wrong only changes whether an icon is lit, not a
       business invariant
    The boundary still holds: core entity writes **other than these two columns** always go through MCP (call_tool).

★ While the columns are not yet in place, **degrade to an empty list / 503**, never raise 500:
    adding the columns and frontend integration run in parallel, and one 500 blanks the whole page,
    whereas an empty list just means "nothing favourited yet".
"""

from __future__ import annotations

import logging

from . import kernel

log = logging.getLogger("win3.bff.quick_entry")

# Four kinds (Feng fixed three at 22:25 and added "the plan table is needed too" at 22:31).
#   table      target table
#   key        the target's unique key column (= the frontend's entryKey)
#   label      the display-name column (fetched live, so it follows renames naturally)
#   label_sql  optional: display-name expression (agx_plan has no name column, so fall back to description)
TABLES: dict[str, dict[str, str]] = {
    "project": {"table": "agx_project", "key": "project_id", "label": "project_name"},
    "agent": {"table": "agx_agent_registry", "key": "agent_id", "label": "agent_name"},
    "session": {"table": "agx_session", "key": "session_id", "label": "session_name"},
    # agx_plan has no plan_name: the first 60 chars of the description (same definition as realdata.plans), or plan_id when empty
    "plan": {
        "table": "agx_plan",
        "key": "plan_id",
        "label": "plan_id",
        "label_sql": "LEFT(COALESCE(NULLIF(description, ''), plan_id), 60)",
    },
}


def _label_expr(meta: dict[str, str]) -> str:
    return meta.get("label_sql") or meta["label"]

ENTRY_TYPES = tuple(TABLES)

_MAX_ROWS = 500


def _ready() -> bool:
    """Whether the is_quick column is in place on all three tables (cache successes; failures are not cached, so adding the column self-heals)."""
    global _READY  # noqa: PLW0603
    if _READY:
        return True
    try:
        for meta in TABLES.values():
            kernel.query(f"SELECT is_quick FROM {meta['table']} LIMIT 1")
        _READY = True
        return True
    except Exception as e:  # noqa: BLE001
        log.info("quick columns not ready: %s", e)
        return False


_READY = False


def _check_type(entry_type: str) -> dict[str, str]:
    meta = TABLES.get(entry_type)
    if not meta:
        raise ValueError(f"entry_type must be one of {ENTRY_TYPES}")
    return meta


def list_entries() -> list[dict]:
    """All marked quick entries (the kinds merged, ordered by type + quick_order).

    `label` is fetched live from the target table -- when the target is renamed this follows, leaving no stale snapshot.
"""
    if not _ready():
        return []
    out: list[dict] = []
    for entry_type, meta in TABLES.items():
        try:
            rows = kernel.query(
                f"""SELECT {meta['key']} AS entry_key, {_label_expr(meta)} AS label,
                           quick_order
                      FROM {meta['table']}
                     WHERE is_quick = 1
                     ORDER BY quick_order ASC, label ASC
                     LIMIT %s""",
                (_MAX_ROWS,),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("quick list %s failed: %s", entry_type, e)
            continue
        for r in rows:
            out.append(
                {
                    # No auto-increment id any more: type:key is the stable identity (the frontend keys on it)
                    "entryId": f"{entry_type}:{r.get('entry_key') or ''}",
                    "entryType": entry_type,
                    "entryKey": str(r.get("entry_key") or ""),
                    "label": str(r.get("label") or ""),
                    "ownerAgentId": "",  # the column era has no owner, one global set
                    "sortOrder": int(r.get("quick_order") or 0),
                    "createdAt": "",
                }
            )
    return out


def is_quick(entry_type: str, entry_key: str) -> bool:
    meta = TABLES.get(entry_type)
    if not meta or not entry_key or not _ready():
        return False
    try:
        rows = kernel.query(
            f"SELECT 1 FROM {meta['table']} WHERE {meta['key']} = %s AND is_quick = 1 LIMIT 1",
            (entry_key,),
        )
        return bool(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("quick is_quick failed: %s", e)
        return False


def add_entry(entry_type: str, entry_key: str, label: str = "") -> dict:
    """Mark as quick (idempotent): UPDATE one row's marker.

    The `label` argument is kept only for signature compatibility -- names are fetched live from the target
    table, no snapshot is stored. When the target does not exist, rowcount=0 -> an explicit error (rather than
    silently succeeding while the page shows nothing).
"""
    meta = _check_type(entry_type)
    key = (entry_key or "").strip()
    if not key:
        raise ValueError("entry_key is required")
    if not _ready():
        raise RuntimeError("核心表尚未添加 is_quick 字段，请先加列")

    # ★ Confirm the target exists before setting the marker.
    #   Do not judge by UPDATE's rowcount alone: MySQL returns 0 when "the value is unchanged" (already 1),
    #   which collides with the 0 for "this row does not exist" -- a repeated star click would be misreported as "not found".
    try:
        exists = kernel.query(
            f"SELECT 1 FROM {meta['table']} WHERE {meta['key']} = %s LIMIT 1", (key,)
        )
        if not exists:
            raise ValueError(f"{entry_type} 不存在：{key}")
        kernel.execute(
            f"UPDATE {meta['table']} SET is_quick = 1 WHERE {meta['key']} = %s",
            (key,),
        )
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("quick add failed: %s", e)
        raise RuntimeError(f"收藏失败：{e}") from e

    rows = kernel.query(
        f"SELECT {_label_expr(meta)} AS label, quick_order FROM {meta['table']} "
        f"WHERE {meta['key']} = %s LIMIT 1",
        (key,),
    )
    row = rows[0] if rows else {}
    return {
        "entryId": f"{entry_type}:{key}",
        "entryType": entry_type,
        "entryKey": key,
        "label": str(row.get("label") or label or ""),
        "ownerAgentId": "",
        "sortOrder": int(row.get("quick_order") or 0),
        "createdAt": "",
    }


def remove_entry(entry_type: str, entry_key: str) -> bool:
    """Unmark: set the marker back to 0 (quick_order is not cleared, so the order is remembered on re-favouriting)."""
    meta = TABLES.get(entry_type)
    if not meta or not entry_key or not _ready():
        return False
    try:
        n = kernel.execute(
            f"UPDATE {meta['table']} SET is_quick = 0 WHERE {meta['key']} = %s AND is_quick = 1",
            (entry_key,),
        )
        return n > 0
    except Exception as e:  # noqa: BLE001
        log.warning("quick remove failed: %s", e)
        return False
