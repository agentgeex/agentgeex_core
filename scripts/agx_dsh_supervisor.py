#!/usr/bin/env python3
"""AgentGeeX DSH bridge - launcher (supervisor)

Why this layer exists (decided by the owner on 2026-11-17)

    The owner's words: "a host starts exactly one daemon and exactly one DSH. On the openclaw side
    it is config-driven - however many entries the config has, that is how many run. DSH should work
    the same way: it should also be config-driven."

    The facts before the change (measured): `daemon.start_dsh_client()` started a bridge
    **unconditionally**, taking host_id from the top-level config.host_id. So:
      . A host without DSH (test_host_mac): still started a bridge against 127.0.0.1:8791,
        where no dsh web exists -> pure spin.
      . A host with DSH (188): the bridge started by the daemon took host_id=test_host_srv (top-level
        default) and consumed agx:dsh:task:test_host_srv - while tasks actually go to test_host_srv.
        The one doing the work was a separate hand-written systemd service. **Two bridges, one working.**

    The root cause is not the bridge but "who decides how many to start": the openclaw side has long
    been decided by config.gateways (one ws_client process pins N connections), while DSH lacked that step.

    So this file does exactly what agx_state_relay.py does:
        the daemon starts one of these  ->  it starts N according to config.gateways
    The daemon side does not need to know N, and does not drift with the config.

Decision basis: agent_type (fixed as a config contract on 2026-11-17)

    Each gateways entry carries an ``agent_type``: ``openclaw`` or ``dsh``.
      . openclaw -> ws_client starts a WS connection (not this file's business)
      . dsh      -> this file starts one agx_dsh_client.py bridge
    Normalization goes through config_loader.gateway_entries() so that four places (ws_client /
    daemon / state_relay / this file) share one decision instead of each writing its own.

    Address of a DSH entry (optional override, written inside that entry's ``dsh`` object)::

        {"host_id": "test_host_srv", "agent_type": "dsh",
         "dsh": {"host": "192.168.0.188", "port": 8791, "profile": "web"}}

    Without a ``dsh`` object the top-level ``config.dsh`` block is used - a single-DSH machine needs
    no duplication. The bridge already supports these env overrides (AGX_DSH_HOST / AGX_DSH_PORT /
    ...); this file only passes them through.

Responsibilities (deliberately narrow)

    * Process only: start, supervise, stop. It parses no DSH protocol and writes no Redis.
      All bridge logic lives in agx_dsh_client.py; this file copies none of it.
    * It does not start children with --daemon: the child stays in the foreground and this process
      holds the handle. Nested daemonization = lost parent/child relation = orphans nobody reaps.
    * Before starting, it sweeps orphans (every agx_dsh_client.py in ps except its own children) and
      removes them. The bridge has no argparse and does not take host_id from the command line (env
      only), so ps cannot tell which host a bridge belongs to - it can only do "if it is not mine, kill
      it". Safety comes from the single-instance lock: while the lock is held, any foreign bridge is
      leftover or manually started and should be cleaned up.

Config hot reload

    On SIGHUP -> re-read config and reconcile against the new gateways:
      start the added ones, stop the removed ones, leave the unchanged ones running.
    ``kill -HUP <supervisor_pid>`` is enough; no daemon restart needed.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import (  # noqa: E402
    AGENT_TYPE_DSH,
    config,
    gateway_entries,
)

# * Logs go to ~/.agx/runtime_watch uniformly, consistent with state_relay.
#   Why not PROJECT_ROOT: ~/.agx/scripts is a symlink into the repo,
#   so PROJECT_ROOT resolves to the repo dir -> logs land in two different places (state_relay hit this).
RUNTIME_LOG_DIR = Path(os.path.expanduser("~/.agx/runtime_watch"))
LOCK_FILE = RUNTIME_LOG_DIR / "dsh_supervisor.lock"

TICK = 2.0                 # main loop tick (seconds)
MIN_BACKOFF = 5.0          # if the child stayed alive long enough then died -> restart immediately
MAX_BACKOFF = 60.0         # upper bound of consecutive fast failures
FAST_FAIL_SECONDS = 30.0   # dying faster than this means a config/env problem; back off longer

# entry-level dsh override -> env names the bridge understands (the bridge reads os.environ.get("AGX_..."))
_DSH_ENV_MAP = {
    "host": "AGX_DSH_HOST",
    "port": "AGX_DSH_PORT",
    "profile": "AGX_DSH_PROFILE",
    "backend": "AGX_DSH_BACKEND",
    "provider": "AGX_DSH_PROVIDER",
    "model": "AGX_DSH_MODEL",
    "token": "AGX_DSH_TOKEN",
    "token_file": "AGX_DSH_TOKEN_FILE",
    "max_pool": "AGX_DSH_MAX_POOL",
    "max_concurrent": "AGX_DSH_MAX_CONCURRENT",
    "idle_ttl_s": "AGX_DSH_IDLE_TTL",
    "autostart": "AGX_DSH_AUTOSTART",
    "check_version": "AGX_DSH_CHECK_VERSION",
    "reap_interval": "AGX_DSH_REAP_INTERVAL",
    "agent_root": "AGX_DSH_AGENT_ROOT",
    "node": "AGX_DSH_NODE",
    "collect_interval": "AGX_DSH_COLLECT_INTERVAL",
    "collect_startup": "AGX_DSH_COLLECT_STARTUP",
    "collect_tools_dir": "AGX_DSH_COLLECT_TOOLS_DIR",
    "watch_interval": "AGX_DSH_WATCH_INTERVAL",
    "watch_delay": "AGX_DSH_WATCH_DELAY",
    "live_ttl_s": "AGX_DSH_LIVE_TTL",
}

# host_id -> {"popen":..., "fh":..., "started_at":..., "backoff":...}
_children: dict[str, dict] = {}
_retry: dict[str, dict] = {}
_stopping = False
_reload = False


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# -- config parsing ------------------------------------------------


def _dsh_entries() -> list[dict]:
    """How many DSH bridges this host should start - decided solely by entries with agent_type=dsh in config.gateways.

    0 entries -> empty list -> main() exits immediately (this host carries no DSH; the daemon side has a
    quiet-period fallback so it will not restart forever and flood the log).
    """
    out: list[dict] = []
    for e in gateway_entries():
        if e["skip"]:
            # problems with openclaw entries are reported by ws_client; only dsh matters here
            if e["agent_type"] == AGENT_TYPE_DSH:
                log(f"跳过 DSH 条目 | {e['entry']} | {e['skip']}", "WARN")
            continue
        if e["agent_type"] != AGENT_TYPE_DSH:
            continue
        out.append({"host_id": e["host_id"], "dsh": e["dsh"] or {}, "entry": e["entry"]})
    return out


def _bridge_script() -> Path | None:
    candidate = SCRIPT_DIR / "agx_dsh_client.py"
    return candidate if candidate.is_file() else None


def _child_env(hid: str, dsh_overrides: dict) -> dict:
    """Child environment: inherited + this entry's host_id + entry-level dsh overrides.

    * host_id must be passed per entry, never the top-level default - that was the deadliest bug
      before the change: the bridge started by the daemon took the top-level host_id=test_host_srv and
      consumed agx:dsh:task:test_host_srv, while tasks went to test_host_srv, so it never received any.
    """
    env = {**os.environ}
    env["AGX_DAEMON_HOST_ID"] = hid
    for key, env_name in _DSH_ENV_MAP.items():
        val = dsh_overrides.get(key)
        if val is None or val == "":
            continue
        env[env_name] = str(val)
    return env


# -- orphan cleanup -----------------------------------------------


def _sweep_orphans() -> None:
    """Kill agx_dsh_client.py processes that do not belong to this supervisor.

    * Why "if it is not mine, kill it" rather than matching by host_id:
      the bridge has no argparse and reads host_id from env only, so ps cannot show the host -
      without a distinguishing signal, do not pretend to distinguish.
      Safety comes from the single-instance lock: while the lock is held, any foreign bridge is a
      leftover or manually started.

    * Matching must be strict (state_relay learned this the hard way: substring matching killed its own shell):
        1. basename of argv[0] starts with python
        2. some argument's basename is exactly agx_dsh_client.py
        3. pid is not in this process's child table
    """
    try:
        out = subprocess.run(
            ["ps", "axww", "-o", "pid=,args="],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log(f"孤儿扫描失败（不阻塞启动）| {e}", "WARN")
        return

    mine = {ch["popen"].pid for ch in _children.values() if ch.get("popen")}
    me = os.getpid()
    killed = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, args = int(parts[0]), parts[1]
        if pid == me or pid in mine:
            continue

        argv = args.split()
        if not argv or not os.path.basename(argv[0]).startswith("python"):
            continue
        if not any(os.path.basename(a) == "agx_dsh_client.py" for a in argv):
            continue

        try:
            os.kill(pid, signal.SIGTERM)
            log(f"清理遗留 DSH 桥 | pid={pid}")
            killed += 1
        except OSError as e:
            log(f"清理失败 | pid={pid} | {e}", "WARN")

    if killed:
        time.sleep(2.0)


# -- child process lifecycle --------------------------------------


def _spawn(item: dict, backoff: float = MIN_BACKOFF) -> None:
    hid = item["host_id"]
    bridge = _bridge_script()
    if bridge is None:
        log("DSH 桥脚本不存在，无法启动", "ERROR")
        return

    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = RUNTIME_LOG_DIR / f"dsh_bridge_{hid}.log"

    fh = open(log_file, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(bridge)],
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
            # same process group as the parent: taken down together by systemd with the daemon, no orphans
            start_new_session=False,
            env=_child_env(hid, item.get("dsh") or {}),
        )
    except OSError as e:
        fh.close()
        log(f"{hid} 启动失败 | {e}", "ERROR")
        return

    _children[hid] = {
        "popen": proc, "fh": fh, "started_at": time.time(), "backoff": backoff,
    }
    addr = (item.get("dsh") or {})
    log(
        f"{hid} 已启动 | pid={proc.pid} | "
        f"dsh={addr.get('host', '(顶层 config.dsh)')}:{addr.get('port', '-')} | "
        f"log={log_file.name}"
    )


def _stop_child(hid: str, why: str = "") -> None:
    ch = _children.pop(hid, None)
    if not ch:
        return
    proc = ch.get("popen")
    if proc is not None and proc.poll() is None:
        log(f"{hid} 停止中{(' | ' + why) if why else ''} | pid={proc.pid}")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    fh = ch.get("fh")
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass


def _reconcile(desired: list[dict]) -> None:
    """Reconcile against config: start added, stop removed, leave untouched. """
    want = {d["host_id"]: d for d in desired}
    for hid in list(_children.keys()):
        if hid not in want:
            _stop_child(hid, "配置里已移除")
    for hid in list(_retry.keys()):
        if hid not in want:
            _retry.pop(hid, None)
    for hid, item in want.items():
        if hid not in _children and hid not in _retry:
            _spawn(item)


# -- signals ------------------------------------------------------


def _on_signal(signum, _frame) -> None:
    global _stopping, _reload
    if signum == getattr(signal, "SIGHUP", None):
        _reload = True
        return
    _stopping = True


def _acquire_lock():
    """Single-instance lock: two supervisors mean two sets of bridges competing for the same queue."""
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


# -- main flow ----------------------------------------------------


def main() -> int:
    # * must be declared inside the function: main() assigns it, and without global it becomes a local
    #   -> the loop's first read raises UnboundLocalError (state_relay hit this in practice).
    global _stopping, _reload

    lock = _acquire_lock()
    if lock is None:
        log("已有 DSH supervisor 在运行（拿锁失败），本进程退出", "WARN")
        return 0

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _on_signal)

    entries = _dsh_entries()
    if not entries:
        log("本机 gateways 里没有 agent_type=dsh 的条目，DSH 桥不启动（正常）")
        return 0

    log(
        f"启动 | pid={os.getpid()} | DSH 桥 {len(entries)} 个: "
        f"{[e['host_id'] for e in entries]}"
    )
    _sweep_orphans()
    _reconcile(entries)

    try:
        while not _stopping:
            if _reload:
                _reload = False
                fresh = _dsh_entries()
                log(f"收到 SIGHUP，按新配置对账 | {len(fresh)} 个桥")
                _reconcile(fresh)

            now = time.time()

            # (1) reap exited children
            for hid, ch in list(_children.items()):
                proc = ch.get("popen")
                if proc is None:
                    _children.pop(hid, None)
                    continue
                rc = proc.poll()
                if rc is None:
                    continue

                lived = now - ch["started_at"]
                log(f"{hid} 退出 | exit_code={rc} | 存活 {lived:.0f}s", "WARN")
                fh = ch.get("fh")
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
                _children.pop(hid, None)

                backoff = (
                    min(ch["backoff"] * 2, MAX_BACKOFF)
                    if lived < FAST_FAIL_SECONDS else MIN_BACKOFF
                )
                _retry[hid] = {"at": now + backoff, "backoff": backoff}
                log(f"{hid} 将在 {backoff:.0f}s 后重拉")

            # (2) restart when the backoff has elapsed
            for hid, info in list(_retry.items()):
                if now < info["at"]:
                    continue
                _retry.pop(hid, None)
                item = next((e for e in _dsh_entries() if e["host_id"] == hid), None)
                if item is None:
                    log(f"{hid} 已从配置中移除，不再重拉")
                    continue
                _spawn(item, backoff=info["backoff"])

            time.sleep(TICK)
    finally:
        for hid in list(_children.keys()):
            _stop_child(hid, "supervisor 退出")
        try:
            lock.close()
        except OSError:
            pass

    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
