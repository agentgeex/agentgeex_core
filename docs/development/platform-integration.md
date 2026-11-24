# Platform Integration Guide — Adding a New Agent Platform

When you need to support a new Agent platform (such as `codex`, `gemini`, etc.), work through the following checklist item by item.
Currently supported platforms: `openclaw`, `claude_code`, `custom` (fallback).

---

## Integration Checklist

### 1. `agx_struct.json` — shared-task platform declaration

```json
// The platforms field of each task:
"platforms": ["openclaw"]       // visible only to openclaw
"platforms": ["all"]            // visible to all platforms
"platforms": ["openclaw", "codex", "all"]  // visible to the listed platforms
```

- No `platforms` field → visible to all platforms by default
- `_get_shared_task_defs()` filters automatically

---

### 2. MCP — `agx_mcp/mcp_server.py` (4 places)

> **2026-10-24 line-number calibration** (the file has grown to 13,809 lines; old line numbers are off by +400 to +1,600)

| Line | Function | Change |
|------|------|------|
| 1106 | `agx_create_project` | `platform` parameter docs + default value |
| 3224 | `agx_upsert_agent` | `agent_type` parameter docs + default value |
| 1893 | `agx_clone_agent` | `agent_type` fallback default value |
| 3608 | `_build_exec_instruction` | **if/elif chain** — subagent instruction policy for the new platform |

```python
# _build_exec_instruction: subagent prompt only for non-openclaw platforms
if needs_subagent and agent_type not in ("openclaw", "codex"):
    subagent_prompt = "..."
elif needs_subagent and agent_type in ("openclaw", "codex"):
    print(f"[WARN] needs_subagent=True 对 {agent_type} 无效")
```

---

### 3. Daemon — `scripts/agx_daemon.py` (3 places)

> **2026-10-24 line-number calibration**

| Line | Location | Change |
|------|------|------|
| 772 | `get_agent_type()` | default return value `"openclaw"` → no change needed when adding a platform |
| 1231 | Message dispatch | **if/elif chain** — execution path for the new platform (`if agent_type == "openclaw":`) |
| 1301 | tmux branch start command | **if/elif chain** — CLI command for the new platform (`if agent_type == "claude_code":`) |

```python
# Message dispatch:
if agent_type == "openclaw":
    ...  # WS queue path
elif agent_type == "claude_code":
    ...  # tmux + CLI path
elif agent_type == "codex":
    ...  # new platform path
else:
    ...  # fallback

# Start command:
if agent_type == "claude_code":
    shell_cmd = f"... claude --print ..."
elif agent_type == "codex":
    shell_cmd = f"... codex exec ..."
else:
    shell_cmd = f"... openclaw agent --agent {agent_id} ..."
```

**Optional:** `cleanup_orphan_sessions()` (currently L274, formerly `_cleanup_mcp_orphans()`)
It hard-codes a process-name search for `"openclaw-agent"`/`"openclaw-gateway"`; append the new platform's own process if it has one.

---

### 4. Watchdog — `scripts/agx_watchdog.py` (4 places)

> **2026-10-24 line-number calibration** (the file has grown to 2,288 lines)

| Line | Location | Change |
|------|------|------|
| 353 | `get_timeout_for_execution` | All platforms read DB `timeout_seconds` uniformly; when unset, fall back to the configured `task_timeout` |
| 607 | `_capture_diag_info` | **if/elif chain** — diagnostic capture for the new platform |
| 863 | `_send_wake_to_session` | **if/elif chain** — wake channel for the new platform |
| 2186 | `run_watchdog()` | **if/elif chain** — main loop for the new platform's anomaly determination |

> ⚠️ The `check_one_execution` function described in older docs **no longer exists**; the anomaly determination logic now lives inside the `run_watchdog()` main loop.

```python
# Diagnostic capture:
if agent_type == "openclaw":
    live = {"gateway_session": ...}
elif agent_type == "claude_code":
    live = {"tmux_session": ...}
elif agent_type == "codex":
    live = {...}  # new platform diagnostics
else:
    live = {"tmux_session": ...}  # fallback

# Wake channel:
if agent_type == "openclaw":
    return _send_wake_via_ws(...)
elif agent_type == "claude_code":
    return _send_wake_via_tmux(...)
elif agent_type == "codex":
    return _send_wake_via_codex(...)
else:
    return False  # waking is not supported

# Anomaly determination (inside the run_watchdog main loop):
if agent_type == "openclaw":
    # goes through FALLBACK_TIMEOUT + WS session
elif agent_type == "codex":
    # new platform determination logic
else:
    # fallback: tmux timeout
```

---

### 5. Configuration file — `agx_config.json`

> **2026-10-24 correction**: `env.sh` is deprecated; neither the repository nor the deployment side has this file anymore.
> All configuration (including new platform-specific entries) goes into `agx_config.json`.

```json
{
  "platforms": {
    "codex": {
      "cli_path": "/usr/local/bin/codex",
      "api_key": "..."
    }
  }
}
```

---

### 6. Documentation — `docs/`

- `docs/internal/watchdog-openclaw.md` — OpenClaw platform supplement (already present)
- Claude Code watchdog supplement — **none exists and none is planned**; `_capture_diag_info` leaves `template_file` empty for `claude_code` and the alert falls back to the inline text built from `diag_info.live`
- Subagent creation guide for the new platform (if needed)

> ✅ **Fixed on 2026-10-24**: `_capture_diag_info` used to write a **relative path** into `docs/`, but:
> - The watchdog runs in `WORK_DIR`, while an Agent runs in its own workspace
> - When an Agent uses `read`, the relative base is its workspace → only the 4 system agents have a `docs/` symlink
> - Result: non-system agents could not read the template when they received an alert → **silent degradation**
>
> It has been changed to an **absolute path** (`os.path.join(WORK_DIR, "docs", ...)`) plus an existence check (empty string when missing).
> At the same time `install.py`'s `SYMLINK_ITEMS` now includes that file; the docs sub-files in `SYMLINK_ITEMS` are symlinked into
> system agents' workspaces; non-system agents read it directly through the absolute path and need no symlink.
>
> **When adding a new platform**: just put the corresponding `docs/internal/watchdog-<platform>.md` into the repository's `docs/`,
> no change to the Agent workspace structure is needed.

---

## Design Principles

- **Fallback-friendly by default**: an unknown `agent_type` takes the `else` branch (tmux/CLI path) and must not crash
- **Append to the if/elif chain**: with fewer than 5 platforms you do not need a strategy pattern; just add a branch at the end of the chain
- **Source → runtime sync**: after every change, `cp` to `~/.agx/` and restart the MCP

---

## Change Log

| Date | Platform | Change |
|------|------|------|
| 2026-06-03 | `claude_code` | Audit + 3 if/elif platform isolation points (watchdog wake, daemon start command, MCP subagent) |
| 2026-10-24 | — | **Full-document line-number calibration**; `mcp/` → `agx_mcp/`; `_cleanup_mcp_orphans` → `cleanup_orphan_sessions`; `check_one_execution` no longer exists; `env.sh` → `agx_config.json`; moved from `notes/` into `docs/` |
