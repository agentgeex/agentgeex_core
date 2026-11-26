"""quick_cmd.py -- backend support for Quick Commands.

Positioning (per "Quick Command Data Spec v2.1", agentgeex workspace/notes/quick_cmd):
    structure the "frequently used operations" each system agent maintains into
    "parameter form + instruction template", so a user can fill in parameters and
    dispatch with one click instead of hand-writing instructions.

[2026-10-14 Feng: table agx_quick_cmd created -> data source migrated from static JSON to DB tables]
    - Authoritative source = MySQL table agx_quick_cmd (one command per row; supports CRUD).
    - On first read, if the table is empty -> idempotently backfill the seed from
      app/data/quick_cmd.json (14 entries).
    - Table structure (field -> semantic mapping in _row_to_cmd):
        pkid / cmd_id(UNI) / cmd_name / cmd_desc / category /
        target_agent_id / task_labs / run_session_id / template(longtext) /
        params_schema(json) / active_status / created_at / update_dt

This module provides:
    Reads: list_commands() / get_command() / find_by_task() / meta()
    Writes: create_command() / update_command() / archive_command()
    (reads go through kernel.query direct to MySQL; writes go through kernel.execute direct --
      these are BFF-owned tables, MCP has no agx_quick_cmd tool registered, so we write the DB
      directly; see the note on kernel.execute.)

★ Iron rule (spec section 2.3): enforce must include at least backend -- a frontend-only role
  check can be bypassed, so the backend must authoritatively intercept before dispatching.

★ Dispatch semantics (aligned with the 2026-10-07 architecture fix): agx_send_text is forbidden
  for raw text; always wrap through agx_dispatch_task into a task (x_desc description + instruction merged).

★ Target resolution:
    - target_task is a bare task name (no parentheses).
    - delegate_to non-empty (e.g. host_manager) -> the real dispatch target is the agent resolved
      from that "role".
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import kernel
from .actions import ActionError, call_universal_send, normalize_reply
from .kernel import McpToolError, McpUnavailable
from .skill_ref import SkillRefError, resolve_skill_block

log = logging.getLogger("win3.bff.quick_cmd")

_DATA_FILE = Path(__file__).resolve().parent / "data" / "quick_cmd.json"

# Category enum (the frontend maps Chinese labels; unknown categories are shown as-is)
_CATEGORY_ENUM = {
    "network", "system", "notice", "agent", "host", "project",
    "log", "config", "dev", "other",
}


class QuickCmdError(Exception):
    """Quick-command layer error (the route turns it into the unified error body)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ══════════════════════════════════════════════════════════════════
# 1. Static seed data (backfilled when the table is empty)
# ══════════════════════════════════════════════════════════════════
@lru_cache(maxsize=1)
def _raw_seed() -> dict[str, Any]:
    if not _DATA_FILE.is_file():
        return {"commands": []}
    with open(_DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


# ── type whitelist for params_schema (2026-10-19 P3) ────────────────
# Basis: the actual branches in the frontend ParamField (quick-cmd-dialog.tsx) + what production data actually uses.
#   text / textarea / number / bool / date / datetime / enum (options required)
#   select -> compatibility entry: existing production data uses it (e.g. deploy_agent); same semantics as enum but options may be empty
# A new type must be added to the frontend ParamField too, or it silently degrades to a text box.
PARAM_TYPE_WHITELIST = frozenset({
    "text", "textarea", "number", "bool", "date", "datetime", "enum", "select",
})


def _normalize_param(p: dict[str, Any]) -> dict[str, Any]:
    """One params[] item -> the frontend contract (missing fields filled in, conservative defaults).

    [2026-10-14 contract ruling 2/3] Field naming follows the new params_schema contract:
        name / label / type / source / options / default / required / show_if
    - value_source -> source (new contract naming)
    - branch visibility is uniformly show_if (e.g. "scope=='single'")
    - number_hint is unused this round (removed)
    Legacy payload compatibility: when reading, if only value_source exists, fall back to it for source.
"""
    return {
        "name": p.get("name", ""),
        "label": p.get("label", ""),
        "required": bool(p.get("required", False)),
        "type": p.get("type", "text"),
        "placeholder": p.get("placeholder", "") or "",
        "default": p.get("default", ""),
        "options": p.get("options", []) or [],
        "source": p.get("source", p.get("value_source", "")) or "",
        "show_if": p.get("show_if", p.get("showIf", "")) or "",
    }


# ── seed row: one JSON command -> table row (used for backfill) ───────────────
def _seed_row(c: dict[str, Any]) -> dict[str, Any]:
    """One seed JSON command -> a table row.

    [2026-10-14 contract ruling 3/adopted] the template column holds **plain text** = composed_instruction,
    no longer a JSON payload; the params structure goes into params_schema; business fields go into their own columns.
"""
    params = [_normalize_param(p) for p in (c.get("params") or [])]
    return {
        "cmd_id": c.get("cmd_id", ""),
        "cmd_name": c.get("title", ""),
        "cmd_desc": c.get("semantic_skeleton", ""),
        "category": (c.get("category") or _infer_category(c)).lower(),
        "target_agent_id": c.get("owner_agent", ""),
        "task_labs": c.get("target_task", ""),
        "run_session_id": "",
        "template": c.get("composed_instruction", "") or "",
        "params_schema": json.dumps(params, ensure_ascii=False),
        "active_status": 1,
    }


def _infer_category(c: dict[str, Any]) -> str:
    """No category in the seed JSON -> infer a sensible one from target_task / owner."""
    task = (c.get("target_task") or "").lower()
    owner = (c.get("owner_agent") or "").lower()
    if "network" in task:
        return "network"
    if "system_check" in task:
        return "system"
    if "notice" in task:
        return "notice"
    if "agent" in task or "agentfather" in owner:
        return "agent"
    if "host" in task or "host" in owner:
        return "host"
    if "project" in task or "projectmother" in owner:
        return "project"
    if "log" in task:
        return "log"
    return "other"


# ══════════════════════════════════════════════════════════════════
# 2. Table read: row -> frontend contract
# ══════════════════════════════════════════════════════════════════
def _json_load(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback


def _raw_template_text(row: dict[str, Any]) -> str:
    """Return the **authoritative plain text** of the template column.

    [2026-10-14 contract ruling 2/adopted] the template column is a plain-text instruction template (with {slot}).
    - New data: return the text as-is.
    - Legacy pkid 1-14: still a JSON payload -> extract its composedInstruction for compatibility
      (the kernel converts these to plain text separately; readable either way, no error).
"""
    raw = row.get("template")
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("composedInstruction") or "")
    text = str(raw)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return str(obj.get("composedInstruction") or "")
        except Exception:  # noqa: BLE001
            pass
    return text


def _row_to_cmd(row: dict[str, Any]) -> dict[str, Any]:
    """Table row -> frontend contract (QuickCmd).

    [2026-10-14 contract ruling 2/adopted] the authoritative source is now the **table columns**:
        template      -> composedInstruction (plain text)
        params_schema -> params
        business fields -> cmd_name/target_agent_id/task_labs/cmd_desc/category
    Compatibility: if a legacy row's template is still a JSON payload (pkid 1-14), fall back to reading
    the business fields from inside it.
"""
    legacy = _json_load(row.get("template"), {}) or {}
    if not isinstance(legacy, dict):
        legacy = {}

    raw_params = _json_load(row.get("params_schema"), None)
    if raw_params is None:
        raw_params = legacy.get("params")
    params = [_normalize_param(p) for p in (raw_params or [])]

    limit = legacy.get("callerRoleLimit") or {}
    if not isinstance(limit, dict):
        limit = {}

    return {
        "cmdId": str(row.get("cmd_id") or ""),
        "title": str(row.get("cmd_name") or legacy.get("title") or ""),
        "ownerAgent": str(row.get("target_agent_id") or legacy.get("ownerAgent") or ""),
        # ★ 2026-10-26: when is_public=1 there is no ownerAgent (the target is the caller itself)
        "isPublic": int(row.get("is_public") or 0),
        "targetTask": str(row.get("task_labs") or legacy.get("targetTask") or ""),
        "delegateTo": str(legacy.get("delegateTo") or ""),
        "semanticSkeleton": str(row.get("cmd_desc") or legacy.get("semanticSkeleton") or ""),
        "params": params,
        "composedInstruction": _raw_template_text(row),
        "exampleFilled": str(legacy.get("exampleFilled") or ""),
        "notes": str(legacy.get("notes") or ""),
        "callerRoleLimit": {
            "roles": limit.get("roles", []) or [],
            "enforce": limit.get("enforce", "backend") or "backend",
            "note": limit.get("note", "") or "",
        },
        # Extended table fields (for the frontend list)
        "runSessionId": str(row.get("run_session_id") or ""),
        "category": str(row.get("category") or "other"),
        # ★ 2026-10-19 P1: the reply default is computed **on the backend** (same definition as /v1/tasks).
        #   A quick command's reply semantics depend on the reply_mode of the task template its task_labs points at
        #   (fetched via _fetch_rows' LEFT JOIN as task_reply_mode).
        #   Unavailable (empty task_labs / template retired) -> fall back to "yes", matching the kernel default.
        "replyMode": str(row.get("task_reply_mode") or ""),
        "defaultReply": (
            "yes" if (row.get("task_reply_mode") or "result") in ("result", "confirm") else "no"
        ),
        "activeStatus": int(row.get("active_status") if row.get("active_status") is not None else 1),
        "createdAt": str(row.get("created_at") or ""),
        "updatedAt": str(row.get("update_dt") or ""),
        # ── [2026-11-14 Feng] quick commands come in two kinds ──
        #  task = dispatch a task with parameters (creates an execution); text = a stored snippet of common wording,
        #  where one click only copies the template text into the input box and **creates no execution**.
        "sendMode": "text" if str(row.get("send_mode") or "") == "text" else "task",
        "sortOrder": int(row.get("sort_order") if row.get("sort_order") is not None else 100),
        "useCount": int(row.get("use_count") if row.get("use_count") is not None else 0),
        "lastUsedAt": str(row.get("last_used_at") or ""),
    }


# ══════════════════════════════════════════════════════════════════
# 2.1 Table read interfaces
# ══════════════════════════════════════════════════════════════════
def _fetch_rows(
    active_only: bool = False,
    agent_id: str = "",
    include_shared_text: bool = False,
) -> list[dict[str, Any]]:
    """Read the table. Passing agent_id only filters "is this command in that agent's list".

    ★ `include_shared_text=True` (2026-11-14 Feng) --
      "these commands do not belong to one agent, they are usable by everyone, so list them too"
      Text snippets (send_mode='text') **dispatch to no agent at all**; they are just ready-made
      wording stored in the DB, so "owning agent" for them only means "who wrote it", not "who may use it".
      Under this switch, besides "this agent's own", **all** text snippets are returned (the frontend
      shows them in two groups).
      Warning: only text is opened up. task commands are still judged strictly by ownership/public
      availability -- that path really dispatches, and opening it would hand someone else's task
      template to everyone to click.

    ★ Definition (fixed 2026-10-27):
      `is_public` means "a shared template that the clicker executes themselves", **not "usable by every agent"**.
      A public command counts as an agent's only when that agent **genuinely has an active task with the same task_labs** under its name:

        (1) private: is_public=0 AND target_agent_id = that agent
        (2) public:  is_public=1 AND an active task with the same task_labs exists under that agent

      This is **not a permission boundary** (webtools is a super-individual with unlimited rights); it is
      an **ownership test** for "is this command meant for me" -- no match, no display; there is no
      greyed-out "visible but not clickable" state and no role-based blocking.

    ★ Root causes fixed the same day (both, neither optional):
      (1) the old WHERE had only `q.target_agent_id = agent_id` -> every public command (owner NULL) vanished;
      (2) `list_commands()` had a second filter `c["ownerAgent"] == agent_id` ->
          a public command's ownerAgent is an empty string, so they vanished again. (2) is deleted;
          the rule now lives in exactly one place.

    reply_mode (reply semantics) is resolved through a double JOIN: prefer "the caller's own task with
    the same labs", fall back to "the owner's task with the same labs". A public command has owner NULL
    -> the former path applies; when the caller has no such task both are empty and _row_to_cmd falls
    back to "yes" (matching the kernel default).
"""
    conds: list[str] = []
    where_params: list[Any] = []
    if active_only:
        conds.append("q.active_status = 1")
    if agent_id:
        shared_text = "\n              OR q.send_mode = 'text'" if include_shared_text else ""
        conds.append(
            f"""(
              (q.is_public = 0 AND q.target_agent_id = %s)
              OR (q.is_public = 1 AND EXISTS (
                    SELECT 1 FROM agx_task_base tx
                     WHERE tx.agent_id = %s
                       AND tx.task_labs = q.task_labs
                       AND tx.active_status = 1)){shared_text}
            )"""
        )
        where_params.extend([agent_id, agent_id])
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    try:
        return kernel.query(
            f"""SELECT q.cmd_id, q.cmd_name, q.cmd_desc, q.category,
                       q.target_agent_id, q.is_public, q.task_labs, q.run_session_id, q.template,
                       q.params_schema, q.active_status, q.created_at, q.update_dt,
                       q.send_mode, q.sort_order, q.use_count, q.last_used_at,
                       COALESCE(t_use.reply_mode, t_own.reply_mode) AS task_reply_mode
                  FROM agx_quick_cmd q
                  LEFT JOIN agx_task_base t_use
                    ON t_use.agent_id = %s
                   AND t_use.task_labs = q.task_labs
                   AND t_use.active_status = 1
                  LEFT JOIN agx_task_base t_own
                    ON t_own.agent_id = q.target_agent_id
                   AND t_own.task_labs = q.task_labs
                   AND t_own.active_status = 1
                  {where}
                 ORDER BY q.send_mode, q.is_public, q.category, q.sort_order, q.cmd_name""",  # noqa: S608
            tuple([agent_id or ""] + where_params),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("fetch rows failed: %s", e)
        return []


def ensure_seeded() -> int:
    """Backfill the JSON seed when the table is empty (idempotent). Returns the number of rows actually inserted."""
    try:
        rows = kernel.query("SELECT COUNT(*) AS n FROM agx_quick_cmd")
    except Exception as e:  # noqa: BLE001
        log.warning("quick_cmd count failed: %s", e)
        return 0
    n = int((rows[0].get("n") if rows else 0) or 0)
    if n > 0:
        return 0
    inserted = 0
    for c in _raw_seed().get("commands", []):
        r = _seed_row(c)
        if not r["cmd_id"]:
            continue
        try:
            kernel.execute(
                """INSERT INTO agx_quick_cmd
                     (cmd_id, network_id, cmd_name, cmd_desc, category,
                      target_agent_id, task_labs, run_session_id, template,
                      params_schema, active_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    r["cmd_id"], "", r["cmd_name"], r["cmd_desc"],
                    r["category"], r["target_agent_id"], r["task_labs"],
                    r["run_session_id"], r["template"], r["params_schema"],
                    r["active_status"],
                ),
            )
            inserted += 1
        except Exception as e:  # noqa: BLE001
            log.warning("seed quick_cmd %s failed: %s", r["cmd_id"], e)
    if inserted:
        log.info("quick_cmd seeded %d commands from JSON", inserted)
    return inserted


def list_commands(
    active_only: bool = False,
    agent_id: str = "",
    include_shared_text: bool = False,
) -> list[dict[str, Any]]:
    """Quick-command list (table-driven).

    Without agent_id -> all of them (for the admin page; 60 active rows, nothing hidden).
    With agent_id   -> that agent's list = private ownership ∪ public (an active task with the same
                       task_labs exists under its name), each row marked `available: true`.

    ★ Fixed 2026-10-27: this used to run a second
      `[c for c in cmds if c["ownerAgent"] == agent_id]` after the SQL; a public command's ownerAgent
      is an empty string -> the second filter dropped them all (so the SQL fix went to waste). Deleted;
      availability is decided **in exactly one place** (the WHERE in _fetch_rows), avoiding two definitions.
"""
    try:
        rows = _fetch_rows(
            active_only=active_only,
            agent_id=agent_id,
            include_shared_text=include_shared_text,
        )
        if not rows and not agent_id:
            ensure_seeded()
            rows = _fetch_rows(active_only=active_only)
    except Exception as e:  # noqa: BLE001
        log.warning("list_commands query failed, fallback to seed: %s", e)
        out = [_row_to_cmd(_seed_row(c)) for c in _raw_seed().get("commands", [])]
        if not agent_id:
            return out
        # The fallback path uses the same rule (no SQL available -> fetch this agent's task_labs set first)
        labs = _agent_task_labs(agent_id)
        return [
            c for c in out
            if (c.get("isPublic") and c.get("targetTask") in labs) or c["ownerAgent"] == agent_id
        ]
    cmds = [_row_to_cmd(r) for r in rows]
    if agent_id:
        for c in cmds:
            c["available"] = True
    return cmds


def _agent_task_labs(agent_id: str) -> set[str]:
    """The set of task_labs of active tasks under this agent (the ownership test for public quick commands).

    ★ Only used by _fetch_rows' fallback when SQL is unavailable; with SQL working, an EXISTS subquery
      decides directly, and both must agree (private ownership ∪ public with the same labs under its name).
"""
    try:
        rows = kernel.query(
            "SELECT DISTINCT task_labs FROM agx_task_base WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )
        return {str(r.get("task_labs") or "") for r in rows if r.get("task_labs")}
    except Exception as e:  # noqa: BLE001
        log.warning("agent task labs query failed: %s", e)
        return set()


def get_command(cmd_id: str, active_only: bool = False) -> dict[str, Any] | None:
    if not cmd_id:
        return None
    where = "AND active_status = 1" if active_only else ""
    try:
        rows = kernel.query(
            f"""SELECT cmd_id, cmd_name, cmd_desc, category,
                       target_agent_id, is_public, task_labs, run_session_id, template,
                       params_schema, active_status, created_at, update_dt,
                       send_mode, sort_order, use_count, last_used_at
                  FROM agx_quick_cmd WHERE cmd_id = %s {where} LIMIT 1""",  # noqa: S608
            (cmd_id,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("get_command failed: %s", e)
        rows = []
    if rows:
        return _row_to_cmd(rows[0])
    # Fall back to the seed (table unavailable / no match)
    for c in _raw_seed().get("commands", []):
        if c.get("cmd_id") == cmd_id:
            return _row_to_cmd(_seed_row(c))
    return None


def find_by_task(
    agent_id: str = "",
    task_labs: str = "",
    task_name: str = "",
) -> dict[str, Any] | None:
    """[ownership rework 2026-10-08] Reverse-look-up a quick command's config by task identity.

    Match rules (highest priority first):
        1) owner_agent == agent_id AND target_task == task_labs (primary)
        2) only target_task == task_labs (lenient match)
        3) target_task == task_name (compatibility)
"""
    cmds = list_commands(active_only=True)
    labs = (task_labs or "").strip()
    name = (task_name or "").strip()
    aid = (agent_id or "").strip()

    if labs:
        if aid:
            for c in cmds:
                if c["ownerAgent"] == aid and c["targetTask"] == labs:
                    return c
        for c in cmds:
            if c["targetTask"] == labs:
                return c
    if name:
        for c in cmds:
            if c["targetTask"] == name:
                return c
    return None


def meta() -> dict[str, Any]:
    """Meta information: the data source is now a table; spec/collection time still come from the seed file for reference."""
    r = _raw_seed()
    try:
        rows = kernel.query("SELECT COUNT(*) AS n FROM agx_quick_cmd")
        total = int((rows[0].get("n") if rows else 0) or 0)
    except Exception:  # noqa: BLE001
        total = 0
    return {
        "group": r.get("group", ""),
        "group_name": r.get("group_name", ""),
        "spec_version": r.get("spec_version", ""),
        "collected_at": r.get("collected_at", ""),
        "source": "agx_quick_cmd",
        "total": total,
    }


# ══════════════════════════════════════════════════════════════════
# 3. Role resolution (caller -> roles)
# ══════════════════════════════════════════════════════════════════
def roles_of(caller: str) -> list[str]:
    caller = (caller or "").strip()
    if not caller:
        return []
    try:
        rows = kernel.query(
            "SELECT agent_role FROM agx_agent_registry WHERE agent_id = %s LIMIT 1",
            (caller,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("roles_of query failed: %s", e)
        return []
    if not rows:
        return []
    role = str(rows[0].get("agent_role") or "").strip()
    return [role] if role else []


# ══════════════════════════════════════════════════════════════════
# 4. Assembling + dispatch (authoritative) -- [2026-10-14 converged: moved down into the kernel's agx_quick_cmd_send]
#     The former local compose()/_missing_required()/_invalid_enum() and the two-stage
#     (render + dispatch) are deleted; validation + assembling + dispatch are all done by one kernel method.
# ══════════════════════════════════════════════════════════════════
# [2026-10-15 convergence 2: run() -> a single agx_quick_cmd_send]
#   The kernel's agx_quick_cmd_send already = read table + assemble (agx_quick_cmd_render) + dispatch
#   + record quick_cmd_id/quick_cmd_params for the audit trail, in one step (Feng's call: use that tool's existing permission).
#   Kernel error code -> QuickCmdError mapping lives in _send_via_kernel's _ERR_MAP.
_KERNEL_ERR_MAP: dict[str, str] = {
    "CMD_NOT_FOUND": "CMD_NOT_FOUND",
    "PARAM_MISSING": "PARAM_MISSING",
    "PARAM_INVALID": "PARAM_INVALID",
    "CMD_TARGET_MISSING": "CMD_TARGET_MISSING",
    "TASK_NOT_FOUND": "TASK_NOT_FOUND",
    # [2026-10-17] target_session_key ownership check (must belong to the target agent)
    "SESSION_NOT_OWNED": "SESSION_NOT_OWNED",
    # The kernel's @require_level(5) gate rejects (worker has no L5) -- the kernel side normally raises an MCP exception;
    # if returned as an error string it is likewise mapped to PERMISSION_DENIED (friendly 403 copy for the frontend).
    "PERMISSION_DENIED": "PERMISSION_DENIED",
}


def _send_via_kernel(
    caller: str,
    cmd_id: str,
    params: dict[str, Any],
    self_session_id: str = "",
    target_session_key: str = "",
    timeout_seconds: int | None = None,
    exec_name: str = "",
    needs_reply: int | None = None,
    force_no_reply: bool = False,
    instruction_suffix: str = "",
) -> dict[str, Any]:
    """Call the kernel's agx_quick_cmd_send to do **authoritative validation + assembling + dispatch** (the single authoritative path).

    [2026-10-15 Feng's ruling] run() converges to one kernel call, executed under agx_quick_cmd_send's
    existing permission (tool level L5). caller_agent_id = the originator identity the frontend passes
    explicitly (super-terminal doctrine); it is no longer silently swapped for the target agent.
    self_session_id is passed through (so receipts land correctly); when empty the kernel falls back to
    the main session.

    The kernel returns a JSON string:
      {run_uuid, msg_id, cmd_id, target_agent_id, task_labs,
       title, queue, queue_length, task_ref{task_id,task_name}, instruction}
      or {error: ...}

    Error mapping (kernel error -> QuickCmdError): see _KERNEL_ERR_MAP; unknown -> KERNEL_ERROR.
    Kernel unreachable (McpUnavailable) -> KERNEL_UNAVAILABLE.

    ★ Iron rule (2026-10-14): any kernel return containing error or raw (non-JSON) is raised explicitly
      and never silently degraded -- the same class of trap as the 10-08 "empty plan page".
"""
    # ★ Only pass timeout_seconds when it has a value -- an explicit None is rejected by the kernel's pydantic validation
    #   (agx_quick_cmd_send's int parameter does not accept null), surfacing as "kernel returned non-JSON" KERNEL_ERROR.
    kwargs: dict[str, Any] = {
        "caller_agent_id": caller,
        "cmd_id": cmd_id,
        "params": json.dumps(params or {}, ensure_ascii=False),
        "self_session_id": self_session_id or "",
        "target_session_key": target_session_key or "",
    }
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = int(timeout_seconds)
    if exec_name:
        kwargs["exec_name"] = exec_name
    # [2026-10-17 Feng] quick commands also support "does it need a reply": pass 0/1 explicitly to send it (None = follow the template derivation).
    if needs_reply is not None:
        kwargs["needs_reply"] = int(needs_reply)
    if force_no_reply:
        kwargs["force_no_reply"] = True
    # [2026-10-18 Feng] skill reference: a note block the kernel appends after assembling (skill pointer, not the body)
    if instruction_suffix:
        kwargs["instruction_suffix"] = instruction_suffix
    try:
        raw = kernel.call_tool("agx_quick_cmd_send", **kwargs)
    except McpUnavailable as e:
        raise QuickCmdError("KERNEL_UNAVAILABLE", f"内核不可达：{e}") from e
    except McpToolError as e:
        # Kernel permission gate (insufficient L5) etc. rejected by the MCP layer -> PERMISSION_DENIED (frontend 403)
        msg = str(e)
        if "permission" in msg.lower() or "level" in msg.lower() or "denied" in msg.lower():
            raise QuickCmdError("PERMISSION_DENIED", f"当前身份无权执行该指令：{msg}") from e
        raise QuickCmdError("KERNEL_ERROR", msg) from e

    res = _parse_mcp_json(raw)
    if "raw" in res and "error" not in res:
        # Non-JSON (Error:... and the like) -> iron rule: raise explicitly, never let it through silently
        raise QuickCmdError("KERNEL_ERROR", f"内核返回非 JSON：{res.get('raw')}", res)

    # Kernel permission gate: below @require_level(5) it returns a dict (containing permission_denied) instead of raising
    #   -> normalised to PERMISSION_DENIED (friendly 403 copy for the frontend).
    if res.get("permission_denied") or res.get("required_level"):
        raise QuickCmdError(
            "PERMISSION_DENIED",
            str(res.get("error") or "当前身份无权执行该指令"),
            res,
        )

    err = res.get("error")
    if err:
        code = _KERNEL_ERR_MAP.get(str(err))
        if code == "CMD_NOT_FOUND":
            raise QuickCmdError("CMD_NOT_FOUND", f"快捷指令不存在或已停用：{cmd_id}", {"cmd_id": cmd_id})
        if code == "PARAM_MISSING":
            missing = res.get("missing") or [res.get("detail")]
            raise QuickCmdError(
                "PARAM_MISSING",
                f"缺少必填参数：{', '.join(str(m) for m in missing if m)}",
                {"cmd_id": cmd_id, "missing": missing},
            )
        if code == "PARAM_INVALID":
            raise QuickCmdError(
                "PARAM_INVALID",
                str(res.get("detail") or "参数非法"),
                {"cmd_id": cmd_id, "detail": res.get("detail"), "invalid": res.get("invalid")},
            )
        if code:
            raise QuickCmdError(code, str(res.get("detail") or err), res)
        # Not in the enum: recognise permission-ish text (the kernel describes it in Chinese) -> PERMISSION_DENIED; otherwise explicit KERNEL_ERROR
        err_s = str(err)
        if "权限" in err_s or "permission" in err_s.lower() or "level" in err_s.lower():
            raise QuickCmdError("PERMISSION_DENIED", err_s, res)
        raise QuickCmdError("KERNEL_ERROR", err_s, res)
    return res


# ══════════════════════════════════════════════════════════════════
# 5. Utility functions (kernel JSON parsing)
#    Note: [2026-10-15 convergence] target resolution (_resolve_target/_resolve_delegate) and
#    task_id resolution (_lookup_task_id) are deleted -- both are done inside the kernel's agx_quick_cmd_send.
# ══════════════════════════════════════════════════════════════════
def _parse_mcp_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except Exception:  # noqa: BLE001
            return {"raw": raw}
    return {"raw": raw}


# ══════════════════════════════════════════════════════════════════
# 6. Submit: validate + assemble + dispatch (wrapped as a task)
# ══════════════════════════════════════════════════════════════════
def run(
    caller: str,
    cmd_id: str,
    params: dict[str, Any],
    self_session_id: str = "",
    target_session_key: str = "",
    timeout_seconds: int | None = None,
    exec_name: str = "",
    needs_reply: int | None = None,
    force_no_reply: bool = False,
    skill_ids: list[str] | None = None,
    reply: str = "",
    client_request_id: str = "",
) -> dict[str, Any]:
    """Execute a quick command: one kernel agx_quick_cmd_send (read table -> assemble -> dispatch, in one step).

    [2026-10-15 convergence] Feng's ruling: run() converges to agx_quick_cmd_send under that tool's
    existing permission (L5). The previous [old path] = agx_quick_cmd_render + self-issued
    agx_dispatch_task(caller=target_agent) -- which silently swapped the identity for the target agent
    (the BFF's only odd one out); after convergence caller = the originator identity the frontend passes
    explicitly (aligned with the "super terminal" doctrine of actions.py run_task/send_text), and it also
    records quick_cmd_id / quick_cmd_params (the old path did neither).

    [contract ruling 2] No backend role interception any more; "who may execute" is decided by the target
    task template's own permission plus the kernel L5 gate.
    [contract ruling 1] run_session_id is a session_key-shaped **reserved field** (stored, never read).

    [2026-10-19 P5] When reply = "yes"|"no" is non-empty, go through the kernel's unified facade
    agx_universal_send (receipt normalisation/patching all lives in the facade); when empty, keep the old
    path (agx_quick_cmd_send + template derivation).
"""
    cmd_id = (cmd_id or "").strip()
    if not cmd_id:
        raise QuickCmdError("BAD_REQUEST", "缺少 cmd_id")

    # ── skill reference: rendered as a "reference skills" note that the kernel appends after assembling (valid skills only) ──
    suffix = ""
    if skill_ids:
        try:
            suffix = resolve_skill_block(skill_ids)
        except SkillRefError as e:
            raise QuickCmdError(e.code, e.message) from e

    # One kernel call: authoritative validation + assembling + dispatch + audit trail
    if reply:
        try:
            r = normalize_reply(reply)
            res = call_universal_send(
                caller_agent_id=caller,
                mode="quickcmd",
                reply=r,
                from_session=self_session_id or "",
                to_session=target_session_key or "",
                cmd_id=cmd_id,
                params=json.dumps(params or {}, ensure_ascii=False),
                **({"timeout_seconds": int(timeout_seconds)} if timeout_seconds is not None else {}),
                **({"exec_name": exec_name} if exec_name else {}),
                **({"instruction_suffix": suffix} if suffix else {}),
                **({"client_request_id": client_request_id} if client_request_id else {}),
            )
        except ActionError as e:
            # Facade error -> quick-command error (the error code is passed through as-is; the frontend translates by code)
            raise QuickCmdError(e.code, e.message) from e
        # The facade's quickcmd branch returns only the dispatch result, not cmd metadata -> fill it in from the table (for receipt display)
        row = get_command(cmd_id) or {}
        run_uuid = res.get("run_uuid") or ""
        log.info(
            "quick_cmd run(facade): cmd=%s caller=%s run=%s reply=%s idempotent=%s",
            cmd_id, caller, run_uuid, r, bool(res.get("idempotent")),
        )
        return {
            "dispatched": True,
            "cmd_id": cmd_id,
            "title": row.get("title") or "",
            "run_uuid": run_uuid,
            "msg_id": res.get("msg_id") or "",
            "queue": res.get("queue") or "",
            "queue_length": None,
            # ★ Must use the real landing point the kernel returns (the oneshot session); never assemble :main yourself
            "session_key": res.get("session_key") or "",
            "target_agent": row.get("ownerAgent") or "",
            "task_ref": {
                "task_id": "",
                "task_name": row.get("targetTask") or "",
            },
            "instruction": row.get("composedInstruction") or "",
            "reply": r,
            "needs_reply": int(res.get("needs_reply", 1 if r == "yes" else 0)),
            "idempotent": bool(res.get("idempotent")),
        }

    res = _send_via_kernel(
        caller, cmd_id, params or {},
        self_session_id=self_session_id or "",
        target_session_key=target_session_key or "",
        timeout_seconds=timeout_seconds,
        exec_name=exec_name or "",
        needs_reply=needs_reply,
        force_no_reply=force_no_reply,
        instruction_suffix=suffix,
    )

    run_uuid = res.get("run_uuid") or ""
    target_agent = res.get("target_agent_id") or ""
    task_ref = res.get("task_ref") or {}
    # [2026-10-17 fix] session_key = the **real receiving session key actually persisted** as returned by the kernel.
    #   The old code computed agent:{target_agent}:main itself -> display != actual (it is really the oneshot
    #   agent:{agent}:run_xxx) -- the real bug Feng called out on 2026-10-17. Never compute it yourself again.
    session_key = res.get("session_key") or ""
    log.info(
        "quick_cmd run: cmd=%s caller=%s target=%s task=%s run=%s",
        cmd_id, caller, target_agent, task_ref.get("task_name"), run_uuid,
    )
    return {
        "dispatched": True,
        "cmd_id": res.get("cmd_id") or cmd_id,
        "title": res.get("title") or "",
        "run_uuid": run_uuid,
        "msg_id": res.get("msg_id") or "",
        "queue": res.get("queue") or "",
        "queue_length": res.get("queue_length"),
        "session_key": session_key,
        "target_agent": target_agent,
        "task_ref": task_ref,
        "instruction": res.get("instruction") or "",
    }


# ══════════════════════════════════════════════════════════════════
# 7. Writes: CRUD (table-driven)
# ══════════════════════════════════════════════════════════════════
def _validate_row(row: dict[str, Any]) -> list[str]:
    """Validate required fields on a **table row**, returning a list of errors (empty = pass).

    [2026-10-14] template is now plain text -> validation looks at columns (cmd_name/target_agent_id/
    task_labs/template) + params_schema.

    [2026-10-19 P3] New params_schema validity check (at the write entry, not on the read side):
    - every parameter item must be an object with a non-empty name
    - type must be in the whitelist (aligned with the 6 kinds the frontend ParamField supports)
    - required must be a bool
    Motivation: the old implementation silently degraded a broken schema (e.g. an unsupported type value
    -> the frontend treated it as a text box); the user believed it worked while the parameter did nothing.
    Stopping it before it reaches the DB is far cheaper than investigating afterwards.
"""
    errs: list[str] = []
    if not str(row.get("cmd_name") or "").strip():
        errs.append("title 不能为空")

    # ★ 2026-10-26 public/private split validation (same rule as the DB CHECK constraint)
    is_public = int(row.get("is_public") or 0)
    has_agent = bool(str(row.get("target_agent_id") or "").strip())
    if is_public and has_agent:
        errs.append("公有指令不能绑 ownerAgent（is_public=1 时目标为调用方自己）")
    if not is_public and not has_agent:
        errs.append("私有指令必须指定 ownerAgent")

    if not str(row.get("task_labs") or "").strip() and row.get("send_mode") != "text":
        errs.append("targetTask 不能为空")
    if not str(row.get("template") or "").strip():
        errs.append("composedInstruction 不能为空")

    raw_schema = _json_load(row.get("params_schema"), []) or []
    if not isinstance(raw_schema, list):
        errs.append("params 必须是数组")
        return errs

    seen_names: set[str] = set()
    for idx, p in enumerate(raw_schema):
        if not isinstance(p, dict):
            errs.append(f"params[{idx}] 必须是对象")
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            errs.append(f"params[{idx}].name 不能为空")
            continue
        if name in seen_names:
            errs.append(f"参数名重复：{name}")
        seen_names.add(name)

        ptype = str(p.get("type") or "text").strip().lower()
        if ptype not in PARAM_TYPE_WHITELIST:
            errs.append(
                f"参数「{name}」的 type 非法：{ptype!r}"
                f"（仅支持 {', '.join(sorted(PARAM_TYPE_WHITELIST))}）"
            )

        if "required" in p and not isinstance(p["required"], bool):
            errs.append(f"参数「{name}」的 required 必须是布尔值")

        if ptype == "enum":
            opts = p.get("options")
            if not opts or not isinstance(opts, list):
                errs.append(f"参数「{name}」type=enum 必须提供非空 options 数组")
    return errs


def _build_row(body: dict[str, Any]) -> dict[str, Any]:
    """Input (frontend contract) -> table columns.

    [2026-10-14 contract ruling 2/adopted] no longer writes a JSON `template`:
        template      <- composedInstruction (plain text)
        params_schema <- params
        business fields <- their columns (cmd_name/target_agent_id/task_labs/cmd_desc/category)
    callerRoleLimit is no longer relied on this round (the gate moved down into the task template), so it
    does not go into template. run_session_id is a session_key-shaped reserved field (empty = main session).
"""
    params = [_normalize_param(p) for p in (body.get("params") or [])]
    category = str(body.get("category") or "other").lower()
    if category not in _CATEGORY_ENUM:
        category = "other"
    # [2026-11-14 Feng] two command kinds: text = a plain-text snippet (no dispatch, no parameters, no task template)
    send_mode = "text" if str(body.get("sendMode") or "") == "text" else "task"
    if send_mode == "text":
        params = []
        task_labs = ""
    else:
        task_labs = str(body.get("targetTask") or "")
    try:
        sort_order = int(body.get("sortOrder") if body.get("sortOrder") is not None else 100)
    except (TypeError, ValueError):
        sort_order = 100
    # ★ 2026-10-26: a public command binds no agent (write NULL, consistent with the DB CHECK constraint)
    #   ★ Must write None rather than an empty string: the DB constraint is chk_qc_public_no_agent
    #     CHECK (is_public=0 OR target_agent_id IS NULL) -- an empty string is not equivalent to NULL,
    #     so writing "" hits error 3819 (constraint violation) directly. Fixed 2026-10-26.
    is_public = int(body.get("isPublic") or 0)
    owner = None if is_public else str(body.get("ownerAgent") or "")
    return {
        "cmd_id": str(body.get("cmdId") or "").strip(),
        "cmd_name": str(body.get("title") or ""),
        "cmd_desc": str(body.get("semanticSkeleton") or ""),
        "category": category,
        "target_agent_id": owner,
        "is_public": is_public,
        "task_labs": task_labs,
        "run_session_id": str(body.get("runSessionId") or ""),
        "template": str(body.get("composedInstruction") or ""),
        "params_schema": json.dumps(params, ensure_ascii=False),
        "send_mode": send_mode,
        "sort_order": sort_order,
        "active_status": int(body.get("activeStatus", 1) if body.get("activeStatus") is not None else 1),
    }


def create_command(body: dict[str, Any]) -> dict[str, Any]:
    """Create a quick command. cmdId is unique; a duplicate is an error."""
    row = _build_row(body)
    errs = _validate_row(row)
    if errs:
        raise QuickCmdError("VALIDATION", "参数校验失败：" + "；".join(errs), {"errors": errs})

    cmd_id = row["cmd_id"]
    if not cmd_id:
        # No cmdId given -> slugify from title.
        # ★ 2026-11-26 fix: a Chinese title slugifies to "" (the regex only keeps [a-z0-9]), and the old code
        #   fell back to the fixed string "cmd" -- so the FIRST Chinese command was created as cmd_id="cmd"
        #   and EVERY later one hit "快捷指令已存在：cmd". That is a bug, not a real duplicate: the user never
        #   supplied an id. An auto-generated id must make itself unique instead of failing the "add" action.
        #   Rule kept: an explicitly supplied cmdId that already exists is still a DUPLICATE error (unchanged).
        base = re.sub(r"[^a-z0-9]+", "_", str(body.get("title") or "").strip().lower()).strip("_") or "cmd"
        cmd_id = base
        for _ in range(20):
            taken = kernel.query("SELECT 1 FROM agx_quick_cmd WHERE cmd_id = %s LIMIT 1", (cmd_id,))
            if not taken:
                break
            cmd_id = f"{base}_{uuid.uuid4().hex[:4]}"
        row["cmd_id"] = cmd_id

    # Uniqueness pre-check (reached only for a supplied cmdId; auto ids above are already unique)
    exist = kernel.query("SELECT cmd_id FROM agx_quick_cmd WHERE cmd_id = %s LIMIT 1", (cmd_id,))
    if exist:
        raise QuickCmdError("DUPLICATE", f"快捷指令已存在：{cmd_id}", {"cmd_id": cmd_id})

    kernel.execute(
        """INSERT INTO agx_quick_cmd
             (cmd_id, network_id, cmd_name, cmd_desc, category,
              target_agent_id, is_public, task_labs, run_session_id, template,
              params_schema, send_mode, sort_order, active_status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row["cmd_id"], "", row["cmd_name"], row["cmd_desc"],
            row["category"], row["target_agent_id"], row["is_public"], row["task_labs"],
            row["run_session_id"], row["template"], row["params_schema"],
            row["send_mode"], row["sort_order"],
            row["active_status"],
        ),
    )
    log.info("quick_cmd created: %s", cmd_id)
    return get_command(cmd_id) or _row_to_cmd(row)


def update_command(cmd_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update a quick command (by cmd_id). Only supplied fields are updated: body is treated as a complete payload."""
    cmd_id = (cmd_id or "").strip()
    if not cmd_id:
        raise QuickCmdError("BAD_REQUEST", "缺少 cmd_id")
    exist = kernel.query("SELECT cmd_id FROM agx_quick_cmd WHERE cmd_id = %s LIMIT 1", (cmd_id,))
    if not exist:
        raise QuickCmdError("CMD_NOT_FOUND", f"快捷指令不存在：{cmd_id}", {"cmd_id": cmd_id})

    row = _build_row({**body, "cmdId": cmd_id})
    errs = _validate_row(row)
    if errs:
        raise QuickCmdError("VALIDATION", "参数校验失败：" + "；".join(errs), {"errors": errs})

    kernel.execute(
        """UPDATE agx_quick_cmd
              SET cmd_name=%s, cmd_desc=%s, category=%s, target_agent_id=%s, is_public=%s,
                  task_labs=%s, run_session_id=%s, template=%s,
                  params_schema=%s, send_mode=%s, sort_order=%s,
                  active_status=%s, update_dt=CURRENT_TIMESTAMP
            WHERE cmd_id=%s""",
        (
            row["cmd_name"], row["cmd_desc"], row["category"], row["target_agent_id"], row["is_public"],
            row["task_labs"], row["run_session_id"], row["template"],
            row["params_schema"], row["send_mode"], row["sort_order"],
            row["active_status"], cmd_id,
        ),
    )
    log.info("quick_cmd updated: %s", cmd_id)
    return get_command(cmd_id) or _row_to_cmd(row)


def archive_command(cmd_id: str) -> dict[str, Any]:
    """Soft delete (active_status=0)."""
    cmd_id = (cmd_id or "").strip()
    if not cmd_id:
        raise QuickCmdError("BAD_REQUEST", "缺少 cmd_id")
    n = kernel.execute(
        "UPDATE agx_quick_cmd SET active_status=0, update_dt=CURRENT_TIMESTAMP WHERE cmd_id=%s",
        (cmd_id,),
    )
    if n == 0 and not kernel.query("SELECT cmd_id FROM agx_quick_cmd WHERE cmd_id=%s LIMIT 1", (cmd_id,)):
        raise QuickCmdError("CMD_NOT_FOUND", f"快捷指令不存在：{cmd_id}", {"cmd_id": cmd_id})
    log.info("quick_cmd archived: %s", cmd_id)
    return {"cmd_id": cmd_id, "activeStatus": 0}
