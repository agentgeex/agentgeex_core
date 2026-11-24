#!/usr/bin/env python3
"""Probe the machine for everything AgentGeeX needs, and report what is missing.

WHY THIS EXISTS
---------------
Asking an agent to install AgentGeeX means the agent must first find out what is
already on the machine. Without a fixed probe, every run improvises: one agent
checks `node -v`, another checks `node --version`, a third assumes Node exists
and fails five steps later. This script is the single, boring answer to
"what does this machine have?".

It is deliberately READ-ONLY: it installs nothing, starts nothing, writes nothing.
The installer (human or agent) decides what to do with the answer.

USAGE
-----
    python3 scripts/probe_env.py            # human readable
    python3 scripts/probe_env.py --json      # machine readable (for agents)
    python3 scripts/probe_env.py --json --quiet

Exit code is always 0 — a probe that fails is still a valid answer ("nothing
found"). Callers decide whether the situation is acceptable.

WHAT IT CHECKS
--------------
Required (core node)   : python3 >= 3.12, uv, git, Node.js, npm
Services               : MySQL reachable + version, Redis reachable + version
Optional runtimes      : openclaw, claude, codex, dsh, tmux
Optional tooling       : docker, pm2
Config presence        : ~/.agx/agx_config.json, ~/.agx/agx_creds.env (existence only)

SECURITY
--------
Never prints, reads or reports the content of credential files — only whether
they exist and whether their file mode is 600. Passwords are not probed unless
they are already in the environment/config, and even then only "connected: yes/no"
is reported.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path


# ---- Python version gate ----------------------------------------------------
# This script does not work on 3.8 (it dies on an unrelated error such as
# `unsupported operand type(s) for |` -- measured 2026-11-23 on the system python3 of Ubuntu 20.04).
# Rather than let the user guess at an unrelated TypeError, state plainly how to run it.
if sys.version_info < (3, 10):  # noqa: UP036 - guards the runtime environment, not the style of this file
    sys.exit(
        "ERROR: %s 需要 Python 3.10+（当前 %d.%d）。\n"
        "       请在项目虚拟环境里运行，例如：\n"
        "           uv run python3 scripts/%s"
        % (Path(__file__).name, sys.version_info[0], sys.version_info[1], Path(__file__).name)
    )

MIN_PY = (3, 12)


def _run(cmd: list[str], timeout: int = 8) -> tuple[bool, str]:
    """Run a command, return (ok, first line of output). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or p.stderr or "").strip().splitlines()
        return p.returncode == 0, (out[0] if out else "")
    except Exception:
        return False, ""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _pkg_version(binary: str, args: list[str] | None = None) -> str:
    ok, line = _run([binary] + (args or ["--version"]))
    return line if ok else ""


def probe_python() -> dict:
    v = sys.version_info
    return {
        "found": True,
        "version": f"{v.major}.{v.minor}.{v.micro}",
        "path": sys.executable,
        "ok": (v.major, v.minor) >= MIN_PY,
        "min_required": f"{MIN_PY[0]}.{MIN_PY[1]}",
    }


def probe_binary(name: str, args: list[str] | None = None) -> dict:
    path = _which(name)
    if not path:
        return {"found": False}
    return {"found": True, "path": path, "version": _pkg_version(name, args)}


def probe_mysql() -> dict:
    """Report MySQL client/server availability and version.

    Connection probing uses the same config the rest of the codebase uses, so a
    "connected" answer here matches what the installer will actually get.
    """
    info: dict = {"client": probe_binary("mysql")}

    host, port = "127.0.0.1", 3306
    user = password = database = ""
    cfg_ok = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from config_loader import config  # noqa: WPS433

        host = config.mysql.get("host", "127.0.0.1")
        port = int(config.mysql.get("port", 3306))
        user = config.mysql.get("user", "")
        password = config.mysql.get("password", "")
        database = config.mysql.get("database", "")
        cfg_ok = True
    except Exception as e:  # noqa: BLE001
        info["config_error"] = type(e).__name__

    info.update({"host": host, "port": port, "configured": cfg_ok})
    if not _tcp(host, port):
        info.update({"reachable": False, "connected": False})
        return info

    info["reachable"] = True
    if not cfg_ok:
        info["connected"] = False
        return info

    try:
        import pymysql

        conn = pymysql.connect(host=host, port=port, user=user, password=password,
                               charset="utf8mb4", connect_timeout=4)
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            info["server_version"] = cur.fetchone()[0]
            if database:
                cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (database,))
                table_count = cur.fetchone()[0]
                info["database_exists"] = bool(table_count)
                info["database_tables"] = table_count
        conn.close()
        info["connected"] = True
    except Exception as e:  # noqa: BLE001
        info["connected"] = False
        info["error"] = type(e).__name__
    return info


def probe_redis() -> dict:
    host, port, db, password, username = "127.0.0.1", 6379, 0, "", "default"
    cfg_ok = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from config_loader import config  # noqa: WPS433

        host = config.redis.get("host", "127.0.0.1")
        port = int(config.redis.get("port", 6379))
        db = int(config.redis.get("db", 0))
        password = config.redis.get("password", "")
        username = config.redis.get("username", "default")
        cfg_ok = True
    except Exception:  # noqa: BLE001
        pass

    info: dict = {"host": host, "port": port, "configured": cfg_ok}
    if not _tcp(host, port):
        info.update({"reachable": False, "connected": False})
        return info
    info["reachable"] = True
    if not cfg_ok:
        info["connected"] = False
        return info
    try:
        import redis as redis_lib

        kwargs = {"host": host, "port": port, "db": db, "socket_timeout": 4}
        if password:
            kwargs["password"] = password
            kwargs["username"] = username
        client = redis_lib.Redis(**kwargs)
        client.ping()
        srv = client.info("server")
        info.update({"connected": True, "server_version": srv.get("redis_version", "?")})
    except Exception as e:  # noqa: BLE001
        info.update({"connected": False, "error": type(e).__name__})
    return info


def probe_config_files() -> dict:
    home = Path.home()
    out: dict = {}
    for label, path in (
        ("agx_config.json", home / ".agx" / "agx_config.json"),
        ("agx_creds.env", home / ".agx" / "agx_creds.env"),
    ):
        exists = path.exists()
        entry: dict = {"path": str(path), "exists": exists}
        if exists:
            mode = path.stat().st_mode & 0o777
            entry["mode"] = oct(mode)[2:]
            if label == "agx_creds.env":
                entry["mode_ok"] = mode == 0o600
        out[label] = entry
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    quiet = "--quiet" in sys.argv

    result = {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": probe_python(),
        "required": {
            "uv": probe_binary("uv"),
            "git": probe_binary("git"),
            "node": probe_binary("node"),
            "npm": probe_binary("npm"),
        },
        "services": {
            "mysql": probe_mysql(),
            "redis": probe_redis(),
        },
        "runtimes": {
            "openclaw": probe_binary("openclaw"),
            "claude": probe_binary("claude"),
            "codex": probe_binary("codex"),
            "dsh": probe_binary("dsh"),
            "tmux": probe_binary("tmux"),
        },
        "tooling": {
            "docker": probe_binary("docker"),
            "pm2": probe_binary("pm2"),
        },
        "config": probe_config_files(),
    }

    # ── summary: what the installer must still do ────────────────────────────
    missing: list[str] = []
    if not result["python"]["ok"]:
        missing.append(f"python>={result['python']['min_required']}")
    for name, info in result["required"].items():
        if not info["found"]:
            missing.append(name)
    if not result["services"]["mysql"].get("connected"):
        missing.append("mysql (connectable)")
    if not result["services"]["redis"].get("connected"):
        missing.append("redis (connectable)")
    if not result["runtimes"]["openclaw"]["found"]:
        missing.append("openclaw")
    result["missing_for_core_install"] = missing
    result["ready"] = not missing

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if quiet:
        print("ready" if result["ready"] else "missing: " + ", ".join(missing))
        return 0

    o = result["os"]
    print(f"AgentGeeX environment probe — {o['system']} {o['release']} ({o['machine']})")
    print()
    print(f"  python   {result['python']['version']:12s} {result['python']['path']}"
          f"{'' if result['python']['ok'] else '   ← needs >= ' + result['python']['min_required']}")
    for group in ("required", "runtimes", "tooling"):
        for name, info in result[group].items():
            if info["found"]:
                print(f"  {name:8s} {info.get('version', '')[:60] or '(found)'}")
            else:
                print(f"  {name:8s} -")
    print()
    my = result["services"]["mysql"]
    print(f"  mysql    {'connected' if my.get('connected') else 'NOT connected'}"
          f"  {my.get('host')}:{my.get('port')}"
          + (f"  server {my.get('server_version')}" if my.get("server_version") else ""))
    rd = result["services"]["redis"]
    print(f"  redis    {'connected' if rd.get('connected') else 'NOT connected'}"
          f"  {rd.get('host')}:{rd.get('port')}"
          + (f"  server {rd.get('server_version')}" if rd.get("server_version") else ""))
    print()
    for name, cfg in result["config"].items():
        flags = "exists" if cfg["exists"] else "missing"
        if cfg.get("mode"):
            flags += f", mode {cfg['mode']}"
            if cfg.get("mode_ok") is False:
                flags += "  ← must be 600"
        print(f"  {name:14s} {flags}")
    print()
    if result["ready"]:
        print("  READY — required pieces are present; run ./agx.sh next.")
    else:
        print("  NOT READY — missing: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
