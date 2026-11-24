"""MCP integration routes -- expose a self-check of the BFF<->MCP(SSE) integration capability.

[2026-10-05 Feng's decision: the BFF speaks the MCP protocol (SSE), fully same-origin with MCP]
The BFF connects through `app.mcp_client` (the official mcp SDK, SSE transport) to a standalone SSE MCP
instance (default 127.0.0.1:31890/agentgeex/mcp, streamable-http) and calls mcp_server tool functions over
JSON-RPC; it no longer imports the kernel source in-process (avoiding the historical problems of two cache
copies / two connection pools).

Write operations (next batch): likewise through `kernel.call_tool("agx_xxx", caller_agent_id=<from the
frontend>, ...)` straight to the MCP tool, reusing its @require_level permission and cache write chain.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import kernel
from ..auth import actor_of, current_user
from ..config import AGX_ROOT, settings
from ..mcp_client import McpToolError, McpUnavailable

router = APIRouter(prefix="/mcp", tags=["mcp"])
_auth = Depends(current_user)

# MCP tools the BFF reuses (or already reuses)
USED_TOOLS = [
    "agx_list_by_network",
    "agx_list_sessions",
    "agx_plan_list",
    "agx_get_agent",
    "agx_check_services",
    "agx_upsert_agent",
    "agx_update_agent",
    "agx_clone_agent",
    "agx_dispatch_task",
    "agx_schedule_task",
    "agx_finish_task",
]


@router.get("/tools")
def mcp_tools(user: dict = _auth) -> dict:
    """MCP integration self-check: channel + availability of the target tools."""
    _ = user
    return {
        "integration": {
            # ★ 2026-11-04: it used to hardcode "sse", which stopped being true once we moved to the shared gateway.
            #   Now it reports settings' real value -- a self-check carrying stale information itself is worse than none.
            "transport": settings.mcp_transport,
            "client": "app.mcp_client (official mcp SDK)",
            "tool_call": "kernel.call_tool -> agx_mcp.mcp_server.<tool>",
            "audit": "caller_agent_id 由前端透传",
        },
        "kernel": kernel.ping(),
        "tools": {t: kernel.has_mcp_tool(t) for t in USED_TOOLS},
    }


# ── MCP version self-check (Feng's request, 2026-10-29 15:49) ────────────────────────
#
# Why this endpoint exists:
#   The MCP kernel is updated often, but **an already-running MCP process still runs old code** (the BFF connects to a long-lived SSE
#   instance, which keeps returning the old version until restarted). After every change Feng wants to know "which version
#   is the frontend actually connected to", and previously that meant digging through ps/logs -- costly and easy to misread.
#
# Approach: pass through to the kernel self-check tool `agx_check_services` and surface its
#   mcp_version (version time + change note) / mcp_sha / runtime_timestamp
#   verbatim. **That string is a constant in the kernel code**, so:
#   · old process -> an old version string (you see at a glance that it was not restarted)
#   · new process -> the new version string
# I.e. "the version string itself is the evidence of whether the process is old or new"; no need to compare file mtimes.
#
# No caching: the endpoint's only value is "which process is it right now"; reading a cache would be self-deception.

# "Kernel code vs MCP process" freshness comparison (Feng's real pain point, 2026-10-29)
#
# Why this block is necessary:
#   `agx_check_services`'s mcp_version is a **hardcoded constant** that has not been updated since it was written
#   on 2026-10-22 (whoever changes the code forgets it). Reporting only that string -> always 10-22,
#   and daily kernel updates stay invisible: a misleading gauge.
#
# The real test is "which of the two times is newer":
#   · kernel source file mtime   -- when the code was last changed
#   · MCP process start time     -- which version of the code this process read into memory
#   process started **before** the code change -> old code in memory; it must be restarted.
#
# ★ 2026-11-04 correction: this text used to say "only about the SSE process the BFF connects to", finding it via
#   /tmp/win3_mcp_sse.pid. After moving to the shared gateway (31890), that pid file points at
#   the old 31888 process **we no longer connect to** -- judging freshness from it is reading a bystander's watch.
#   Changed to: **whoever is listening on settings.mcp_url's port is the one to inspect** (transport-agnostic; the fact proves itself).
#   The old pid file is only a fallback for legacy SSE mode.

_PID_FILE = Path(os.environ.get("WIN3_MCP_PID_FILE", "/tmp/win3_mcp_sse.pid"))
_KERNEL_FILE = AGX_ROOT / "agx_mcp" / "mcp_server.py"


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _proc_start_epoch(pid: int, allow_file_fallback: bool = False) -> float | None:
    """MCP process start time (epoch). Falls back to the pid file mtime when ps fails.

    ★ A trap hit on 2026-11-04: `ps -o lstart=` is affected by **locale** --
      on a Chinese-locale host the output is a localized weekday/month form, which `strptime("%a %b %d ...")` cannot parse,
      so it silently falls through to the fallback (pid file mtime) -> taking another process's time as this one's.
      Measured consequence: gateway started 08:04, kernel changed 07:43, judged as "old code" (it was the newest code).
      Worse in the other direction: old code plus a newer pid file mtime is judged "newest code".
      So LC_ALL=C is forced to keep the output in English weekday/month -- the format parsing depends on must be
      specified by us, never on the runtime locale. Which also explains why "a self-check" itself must be checkable.
"""
    try:
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=3, check=False, env=env,
        ).stdout.strip()
        if out:
            # ps dates read "Wed Nov  4 08:04:29 2026" (the day may be double-spaced) -> normalise before parsing
            return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
    except Exception:  # noqa: BLE001 -- a probe failure must not take down the whole self-check endpoint
        pass
    if not allow_file_fallback:
        # The pid found by port has nothing to do with the pid file; using the file's timestamp as its start time conflates two things.
        return None
    try:
        return _PID_FILE.stat().st_mtime
    except OSError:
        return None


def _listener_pids(port: int) -> list[int]:
    """Who is listening on this port (possibly several: a stale process shows up as a double listener)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.split()
        seen: list[int] = []
        for x in out:
            try:
                pid = int(x)
            except ValueError:
                continue
            if pid not in seen:
                seen.append(pid)
        return seen
    except Exception:  # noqa: BLE001
        return []


def _kernel_code_epoch() -> tuple[float | None, str]:
    """Return the time the kernel code **content** last changed (epoch) + the basis used.

    Why a bare mtime is unusable (hit for real on 2026-11-05 22:24):
      `git checkout <branch>` / `git checkout master -- <path>` **rewrite the file** --
      not one character of content changed, yet mtime becomes "just now". So a freshly restarted process is
      judged "started before the code change -> old code in memory", pushing people into "restart again".
      Measured case: mcp_server.py's last commit 16:57:51, gateway started 16:58:09, which should be clean;
      one branch switch at 22:24 pushed mtime to 22:24 -> false alarm.

    The correct test (git can distinguish "content changed" from "merely rewritten"):
      · file has **no uncommitted changes** -> use the time of the last commit touching that file
      · file **has uncommitted changes**    -> use mtime (this time it really was just changed)
      · git unavailable / not in a repo     -> fall back to mtime
"""
    try:
        mtime = _KERNEL_FILE.stat().st_mtime
    except OSError:
        return None, "missing"

    repo = AGX_ROOT
    try:
        rel = _KERNEL_FILE.relative_to(repo).as_posix()
    except ValueError:
        # The kernel file is not in a repo (a temp file injected by unit tests / external deployment) -> git cannot judge; use mtime
        return mtime, "mtime"
    try:
        dirty = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain", "--", rel],
            cwd=str(repo), capture_output=True, text=True, timeout=3, check=False,
        )
        if dirty.returncode != 0:
            return mtime, "mtime"
        if dirty.stdout.strip():
            # uncommitted changes -> genuinely just changed, so mtime is meaningful
            return max(mtime, _git_last_commit_epoch(repo, rel) or 0.0), "mtime(dirty)"
        ce = _git_last_commit_epoch(repo, rel)
        return (ce, "git-commit") if ce else (mtime, "mtime")
    except (OSError, subprocess.SubprocessError):
        return mtime, "mtime"


def _git_last_commit_epoch(repo: Path, rel: str) -> float | None:
    try:
        r = subprocess.run(  # noqa: S603
            ["git", "log", "-1", "--format=%ct", "--", rel],
            cwd=str(repo), capture_output=True, text=True, timeout=3, check=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def code_freshness() -> dict[str, Any]:
    """Compare the kernel source **content change time** with the MCP process start time -> decide whether memory holds old code.

    The content change time is not a bare mtime: git checkout / branch switches rewrite files (content unchanged,
    mtime newer), which would misjudge a freshly restarted process as old code (hit for real on 2026-11-05 22:24).
    The basis is reported in `kernel_change_source`: git-commit / mtime(dirty) / mtime.

    The key names sse_pid / sse_started_at are historical (the frontend reads them); the semantics have changed to
    "the MCP process the BFF actually connects to" -- now the shared gateway.
"""
    from urllib.parse import urlparse

    u = urlparse(settings.mcp_url)
    port = u.port or 80
    out: dict[str, Any] = {
        "kernel_file": str(_KERNEL_FILE),
        "kernel_mtime": None,
        "kernel_change_at": None,
        "kernel_change_source": "mtime",
        "endpoint": settings.mcp_url,
        "port": port,
        "sse_pid": None,
        "sse_started_at": None,
        "stale": None,
        "verdict": "",
    }
    try:
        km = _KERNEL_FILE.stat().st_mtime
        out["kernel_mtime"] = _fmt_ts(km)
    except OSError as e:
        out["verdict"] = f"读不到内核源文件：{e}"
        return out

        # Content change time: git commit time preferred (branch switches/checkouts do not raise false alarms), dirty/mtime as fallback
    km2, src = _kernel_code_epoch()
    if km2 is None:
        out["verdict"] = "读不到内核源文件（已消失）→ 请人工确认"
        return out
    out["kernel_change_at"] = _fmt_ts(km2)
    out["kernel_change_source"] = src

    # (1) find by port (transport-agnostic, most trustworthy)
    pids = _listener_pids(port)
    pid: int | None = pids[0] if pids else None
    out["listeners"] = pids
    if pid is None:
        # (2) legacy SSE fallback: the pid file
        try:
            pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            out["verdict"] = f"端口 {port} 上没有监听进程，pid 文件也取不到 → 请人工确认"
            return out
    out["sse_pid"] = pid
    out["pid_source"] = "port" if pids else "pidfile"
    if len(pids) > 1:
        # Double listening = a stale process was never cleaned up; then "changed the code but still on old code" is very hard to notice -- it must be called out.
        out["verdict"] = f"端口 {port} 上有 {len(pids)} 个监听进程 {pids} → 可能新旧并存，需清理"
        out["stale"] = True
        return out

    started = _proc_start_epoch(pid, allow_file_fallback=not pids)
    if started is None:
        out["verdict"] = f"进程 {pid} 查不到启动时间（可能已退出）→ 请人工确认"
        return out

    out["sse_started_at"] = _fmt_ts(started)
    # 2s tolerance: within the same second the file-write/process-start order is meaningless
    out["stale"] = started < km2 - 2
    out["verdict"] = (
        f"MCP 进程（:{port} pid {pid}）启动早于内核代码改动（判据 {src}） → 内存里是旧码，需重启该服务"
        if out["stale"]
        else f"MCP 进程（:{port} pid {pid}）启动晚于内核代码改动（判据 {src}） → 跑的是最新码"
    )
    return out


@router.get("/check-services")
def mcp_check_services(user: dict = _auth) -> dict[str, Any]:
    """MCP service self-check -- returns the current SSE MCP instance's version info and kernel connectivity."""
    caller = actor_of(user) or "win3_bff"
    try:
        raw = kernel.call_tool("agx_check_services", caller_agent_id=caller)
    except McpUnavailable as e:
        raise HTTPException(
            502,
            {
                "error": {
                    "code": "MCP_UNAVAILABLE",
                    "message": f"MCP 通道不可达（{settings.mcp_url}）：{e}",
                }
            },
        ) from e
    except McpToolError as e:
        raise HTTPException(
            502,
            {"error": {"code": "MCP_TOOL_ERROR", "message": str(e)}},
        ) from e

    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # If it is not JSON, report it as-is; never pretend success (see "no silent degradation")
            data = {"raw": raw}
    if not isinstance(data, dict):
        data = {"raw": data}

    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checked_by": caller,
        "mcp_sse_url": settings.mcp_url,  # key name kept for old frontends; the value now points at the shared gateway
        "mcp_transport": settings.mcp_transport,
        "services": data,
        "code_freshness": code_freshness(),
    }
