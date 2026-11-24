"""Open a project directory in the host's file manager (the project detail page's "open folder").

[2026-10-26 Feng] "If a project has a path, could we make a link that opens that folder?"

Why not a `file://` link:
    browsers **forbid** an http(s) page from navigating to `file://` (Chrome/Firefox both block it silently --
    clicking does nothing and gives no hint) -- doing that would be lying.
So an explicit side channel: the frontend button -> the BFF calls the system opener **on the host it runs on**
(macOS `open` / Windows `explorer` / Linux `xdg-open`).

Security boundary (three rules, all required):
    1. **The path is never accepted from the frontend**: it is looked up by project_id from
       agx_project.source_dir, so it cannot become a "open any path" general-purpose execution entry.
    2. **Directories only**, and the path must be absolute (a relative path resolves against the BFF
       process's cwd, which is uncontrollable).
    3. **By default only loopback may trigger it**: otherwise anyone on the LAN clicking once pops Finder
       on your Mac. Set BFF_REVEAL_ALLOW_REMOTE=1 explicitly for remote triggering.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from . import kernel
from .config import settings

log = logging.getLogger("win3.bff.reveal")

# Opening a directory only -> none of these openers need a shell; arguments are passed as a list (no injection surface)
_OPENER = {
    "darwin": "open",
    "win32": "explorer",
}


class RevealError(Exception):
    """Open failed (argument/environment reason), for the route to turn into an explicit error body."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def project_source_dir(project_id: str) -> str:
    """Fetch source_dir from the DB (never trust a frontend-supplied value)."""
    try:
        rows = kernel.query(
            "SELECT source_dir FROM agx_project WHERE project_id = %s",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        raise RevealError("KERNEL_UNAVAILABLE", f"读取项目失败：{e}", 502) from e
    if not rows:
        raise RevealError("NOT_FOUND", f"项目不存在：{project_id}", 404)
    return str(rows[0].get("source_dir") or "").strip()


def _opener_cmd() -> str:
    return _OPENER.get(sys.platform, "xdg-open")


def reveal_project_dir(project_id: str, client_host: str) -> dict:
    """Open the project's source_dir in the host's file manager.

    Args:
        project_id: project id (the path is looked up by it).
        client_host: request source IP (used for the loopback check).

    Raises:
        RevealError: no directory / directory does not exist / remote not allowed / no opener in this environment.
"""
    if not settings.reveal_allow_remote:
        ip = (client_host or "").strip()
        loopback = ip in {"127.0.0.1", "::1", "localhost", "testclient"}
        if not loopback:
            raise RevealError(
                "REMOTE_FORBIDDEN",
                f"「打开文件夹」只在 BFF 所在主机上生效（本次请求来自 {ip or '未知'}）。"
                "这是宿主机操作，默认不允许远程触发。",
                403,
            )

    raw = project_source_dir(project_id)
    if not raw:
        raise RevealError("NO_SOURCE_DIR", "该项目未设置源码目录（source_dir 为空）", 400)

    # Accept absolute paths only: a relative path resolves against the BFF process cwd, i.e. handing "what to open" to the runtime environment
    if not os.path.isabs(raw):
        raise RevealError("NOT_ABSOLUTE", f"源码目录不是绝对路径，无法定位：{raw}", 400)

    path = Path(os.path.expanduser(raw))
    if not path.exists():
        raise RevealError("DIR_NOT_FOUND", f"目录不存在（可能已移动或删除）：{path}", 404)
    if not path.is_dir():
        raise RevealError("NOT_A_DIR", f"该路径不是文件夹：{path}", 400)

    cmd = _opener_cmd()
    if not _has_cmd(cmd):
        raise RevealError(
            "NO_OPENER",
            f"宿主环境没有可用的文件管理器命令（{cmd}）",
            501,
        )

    try:
        # start_new_session: not parented to the uvicorn process group, so a BFF restart does not kill it
        subprocess.Popen(  # noqa: S603 -- the command and arguments are constants / an already-validated path, no shell
            [cmd, str(path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:  # noqa: BLE001
        raise RevealError("EXEC_FAILED", f"调用 {cmd} 失败：{e}", 500) from e

    log.info("reveal %s -> %s (by %s, opener=%s)", project_id, path, client_host, cmd)
    return {
        "ok": True,
        "projectId": project_id,
        "path": str(path),
        "opener": cmd,
        "host": os.uname().nodename if hasattr(os, "uname") else "",
    }


def _has_cmd(name: str) -> bool:
    """Whether the command exists in PATH (a simple cross-platform probe)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        cand = Path(d) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return True
    return False
