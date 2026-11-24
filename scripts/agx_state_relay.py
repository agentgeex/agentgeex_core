#!/usr/bin/env python3
"""AgentGeeX session-state collector - launcher (supervisor)

Why this layer exists (decided by the owner on 2026-11-05)

    The collector itself, gateway_state_relay.py, is "one connection per gateway":
    each OpenClaw instance knows only the sessions it manages (the WS of 18789 has no sessions of 20789).
    So N instances on one host require N collector processes.

    "How much is N" must not be decided by hand-writing N systemd units - that is hard coding.
    It should be decided by config.gateways: **however many entries, that many processes.**

    And the daemon only understands "one child process" (start / stop / check), so this layer is a supervisor:
        the daemon starts this one  ->  it starts N according to config
    The daemon side does not need to know N and does not drift with the config.

Responsibilities (deliberately narrow)

    * Process only: start, supervise, stop. It parses no WS frame and writes no Redis.
      All collection logic lives in gateway_state_relay.py; this file copies none of it.
    * It does not start children with --daemon: the child stays in the foreground and this process holds
      the handle. Nested daemonization = lost parent/child relation = orphans nobody reaps; after a
      daemon restart the old and new sets both write Redis, conflict counts double and they overwrite
      each other's data.
    * Before starting, it sweeps orphans (gateway_state_relay.py matching this host in ps) and removes
      them. This is the only fallback for "left over from the last abnormal exit" - better to kill one
      extra than to allow two sets side by side.

Token resolution (and why not from the command line)

    The collector deliberately accepts no --token: command-line arguments show up in ps output and are
    visible to any user on the host, which is plaintext leakage. It reads only the local openclaw.json
    pointed to by --config. Mapping host_id to that file is this launcher's job:
      1. the entry explicitly provides openclaw_config (or config) -> use it
      2. ~/.openclaw-<host_id>/openclaw.json exists -> use it (multi-instance profile convention)
      3. fall back to ~/.openclaw/openclaw.json
    A missing file / no token inside -> skip that entry with a WARN.
    Skip rather than retry: a wrong config will not become right after a thousand restarts, it only floods the log.

Config hot reload

    On SIGHUP -> re-read config and reconcile against the new gateways:
      start the added, stop the removed, leave the unchanged running (without breaking existing connections).
    ``kill -HUP <supervisor_pid>`` is enough; no daemon restart needed.
"""

from __future__ import annotations

import fcntl
import json
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

from config_loader import AGENT_TYPE_OPENCLAW, config, gateway_entries  # noqa: E402

STATE_DIR = PROJECT_ROOT / "runtime_watch"
SUPERVISOR_LOG = STATE_DIR / "state_relay_supervisor.log"
LOCK_FILE = STATE_DIR / "state_relay_supervisor.lock"

# * 2026-11-14: child logs all go to ~/.agx/runtime_watch, the same target the daemon
#   redirects my stdout to (it writes SUPERVISOR_LOG under ~/.agx/runtime_watch).
#   Why: PROJECT_ROOT resolves to the repo dir because ~/.agx/scripts is a symlink,
#   so collector logs landed in <repo>/runtime_watch/ while supervisor logs were in
#   ~/.agx/runtime_watch/ - two directories. Measured, and it misled once on the spot:
#   ~/.agx/runtime_watch/user_msg_collect.log was stuck at 10:41 and the collector was judged dead.
#   Run logs of the same kind must live together, otherwise "looking at the wrong file" equals "seeing nothing".
RUNTIME_LOG_DIR = Path(os.path.expanduser("~/.agx/runtime_watch"))

TICK = 2.0                 # main loop tick (seconds): only to notice child exits promptly
MIN_BACKOFF = 5.0          # if the child stayed alive long enough then died -> restart immediately
MAX_BACKOFF = 60.0         # upper bound of consecutive fast failures
FAST_FAIL_SECONDS = 30.0   # dying faster than this means a config/env problem; back off longer

# logical key of the user-message collector. It scans every agent store on the host and is unrelated to host_id,
# so one process is enough - it must not be fanned out per gateway entry the way relay is.
USER_MSG_KEY = "user_msg"
USER_MSG_INTERVAL = 60.0   # polling interval (seconds). Measured ~5.6s per round (after fingerprint skipping took effect)

# host_id -> {"popen":..., "fh":..., "started_at":..., "backoff":...}
_children: dict[str, dict] = {}
# host_id -> {"at": restart timestamp, "backoff": seconds}: exited, waiting for the backoff to elapse
_retry: dict[str, dict] = {}
_stopping = False
_reload = False


# -- logging ----------------------------------------------------
#
# print only, open no file: the daemon already redirected stdout/stderr to
# state_relay_supervisor.log when starting this process. Writing both = every line twice.
# when run manually in the foreground, printing to the terminal is exactly what is wanted.


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


# -- config parsing ----------------------------------------------


def _local_host_id() -> str:
    """Same source as daemon.get_host_id(); no separate rule."""
    hid = config.get("host_id", "")
    if not hid:
        hid = os.environ.get("AGX_DAEMON_HOST_ID", "")
    return str(hid or "").strip()


def _gateway_entries() -> list[dict]:
    """How many collectors this host should start - decided entirely by config.gateways.

    Backward compatible: without a gateways array it falls back to the old single config.gateway,
    in which case behaviour matches the pre-change version (one collector).
    """
    out: list[dict] = []
    gws = config.get("gateways") or []

    if isinstance(gws, list) and gws:
        # * 2026-11-17: switched to config_loader.gateway_entries() for unified normalization.
        #   The collector only handles openclaw; dsh entries have their bridges started by agx_dsh_supervisor,
        #   so they are **silently skipped** here (not a misconfiguration, must not WARN).
        for e in gateway_entries():
            if e["agent_type"] != AGENT_TYPE_OPENCLAW:
                continue
            if e["skip"]:
                log(f"跳过 gateways 条目：{e['skip']} | {e['entry']}", "WARN")
                continue
            hid = e["host_id"]
            if not hid:
                log(f"跳过 gateways 条目：缺 host_id | {e['entry']}", "WARN")
                continue
            out.append({"host_id": hid, "ws_url": e["ws_url"], "entry": e["entry"]})
        return out

    single = config.get("gateway") or {}
    url = str(single.get("ws_url") or "").strip()
    if url:
        hid = _local_host_id()
        if not hid:
            log("旧单值 gateway 已配置，但读不到 host_id，无法启动采集器", "ERROR")
            return []
        out.append({"host_id": hid, "ws_url": url, "entry": {}})
    return out


def _user_msg_script() -> Path | None:
    """The user-message collector lives under tools/ (not scripts/): it is a development tool, not a resident service."""
    candidate = PROJECT_ROOT / "tools" / "user_msg_collect.py"
    return candidate if candidate.is_file() else None


def _user_msg_item() -> dict | None:
    """This host's user-message collector (one per machine). Returns None when the script is absent; does not affect relay."""
    script = _user_msg_script()
    if script is None:
        return None
    return {"host_id": USER_MSG_KEY, "kind": "user_msg", "host": _local_host_id()}


def _desired() -> list[dict]:
    """All child processes this host should have alive: gateway relay (possibly several) + user-message collector (one)."""
    items = list(_gateway_entries())
    um = _user_msg_item()
    if um is not None:
        items.append(um)
    return items


def _resolve_openclaw_config(entry: dict, host_id: str) -> Path | None:
    """Map host_id to the openclaw.json of its profile (the only source of the token)."""
    explicit = str(entry.get("openclaw_config") or entry.get("config") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        candidates.append(Path.home() / f".openclaw-{host_id}" / "openclaw.json")
        candidates.append(Path.home() / ".openclaw" / "openclaw.json")

    for p in candidates:
        if not p.is_file():
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log(f"{host_id} 读配置失败，跳过 | {p} | {e}", "WARN")
            continue
        token = ((cfg.get("gateway") or {}).get("auth") or {}).get("token") or ""
        if not token:
            log(f"{host_id} 的配置里没有 gateway.auth.token，跳过 | {p}", "WARN")
            continue
        return p

    log(f"{host_id} 找不到可用的 openclaw.json（试过 {[str(c) for c in candidates]}），跳过", "WARN")
    return None


def _redis_config_path() -> str:
    """Which Redis the collector writes - the same agx_config.json as the daemon."""
    env = os.environ.get("AGX_CONFIG_PATH", "").strip()
    if env and Path(env).is_file():
        return env
    _p = Path.home() / ".agx" / "agx_config.json"
    if _p.is_file():
        return str(_p)
    return ""


def _relay_script() -> Path | None:
    """The collector itself: prefer the same directory (deployed copy), fall back to the repo tools/ (dev machine)."""
    sibling = SCRIPT_DIR / "gateway_state_relay.py"
    if sibling.is_file():
        return sibling
    legacy = PROJECT_ROOT / "tools" / "gateway_state_relay.py"
    if legacy.is_file():
        return legacy
    return None


# -- orphan cleanup -----------------------------------------------
#
# Why it is mandatory: when the daemon crashes or is killed with kill -9, children may survive.
# If a new supervisor round starts another copy, the same gateway has two connections writing the same keys,
# conflict counts inflate and last_event_at overwrites each other. Better to clear the old one first.


def _sweep_orphans(host_ids: list[str]) -> None:
    """Kill leftover collectors.

    * Matching must be strict: do not merely check "does the command line contain this string".
      The first version used substring matching and killed a bash wrapper process running a same-named
      command (measured: it killed its own shell).
      Now three conditions must all hold:
        1. argv[0] is a python interpreter (not bash / grep / tail / vim)
        2. one argument's basename is exactly gateway_state_relay.py
        3. that script is actually followed by --host-id <the host this machine manages>
    """
    if not host_ids:
        return
    try:
        out = subprocess.run(
            ["ps", "axww", "-o", "pid=,args="],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log(f"孤儿扫描失败（不阻塞启动）| {e}", "WARN")
        return

    me = os.getpid()
    killed = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, args = int(parts[0]), parts[1]
        if pid == me:
            continue

        argv = args.split()
        if not argv or not os.path.basename(argv[0]).startswith("python"):
            continue

        script_at = next(
            (i for i, a in enumerate(argv)
             if os.path.basename(a) == "gateway_state_relay.py"),
            None,
        )
        if script_at is None:
            continue

        tail = argv[script_at + 1:]
        hid = ""
        for i, a in enumerate(tail):
            if a == "--host-id" and i + 1 < len(tail):
                hid = tail[i + 1]
                break
            if a.startswith("--host-id="):
                hid = a.split("=", 1)[1]
                break

        if hid not in host_ids:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            log(f"清理遗留采集器 | pid={pid} | host={hid}")
            killed += 1
        except OSError as e:
            log(f"清理失败 | pid={pid} | {e}", "WARN")

    if killed:
        time.sleep(2.0)


# -- child process lifecycle --------------------------------------


def _spawn(item: dict, backoff: float = MIN_BACKOFF) -> None:
    if item.get("kind") == "user_msg":
        _spawn_user_msg(item, backoff)
        return
    hid = item["host_id"]
    relay = _relay_script()
    if relay is None:
        log("采集器脚本不存在，无法启动", "ERROR")
        return

    cfg_path = _resolve_openclaw_config(item["entry"], hid)
    if cfg_path is None:
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_file = STATE_DIR / f"relay_{hid}.log"
    timing_file = STATE_DIR / f"relay_timing_{hid}.jsonl"

    cmd = [
        sys.executable, str(relay),
        "--host-id", hid,
        "--ws-url", item["ws_url"],
        "--config", str(cfg_path),
        "--bootstrap",
        "--duration", "0",
        "--stalled-min", "5",
        "--timing-log", str(timing_file),
        "--log-level", "INFO",
    ]
    redis_cfg = _redis_config_path()
    if redis_cfg:
        cmd += ["--redis-config", redis_cfg]

    fh = open(log_file, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
            start_new_session=False,   # same process group as the parent: taken down by systemd together with the daemon
            env={**os.environ},
        )
    except OSError as e:
        fh.close()
        log(f"{hid} 启动失败 | {e}", "ERROR")
        return

    _children[hid] = {
        "popen": proc, "fh": fh, "started_at": time.time(), "backoff": backoff,
    }
    log(f"{hid} 已启动 | pid={proc.pid} | {item['ws_url']} | config={cfg_path.name}@{cfg_path.parent.name}")


def _spawn_user_msg(item: dict, backoff: float) -> None:
    """Start the user-message collector (one process scanning every agent store on the host).

    Why hand it to the supervisor instead of a manual nohup: on 2026-11-14 it was found to have been
    silently dead for 3.5 days (KeyError('user') after credentials moved to env; it crashed every round
    without alerting). The same backoff + exit logging + single-instance lock turns "the next crash"
    into a visible event instead of a silently empty table.
    """
    script = _user_msg_script()
    if script is None:
        log("用户消息采集器脚本不存在，无法启动", "ERROR")
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = RUNTIME_LOG_DIR / "user_msg_collect.log"

    cmd = [
        sys.executable, "-u", str(script),
        "--watch",
        "--interval", str(int(USER_MSG_INTERVAL)),
    ]
    host = item.get("host") or ""
    if host:
        cmd += ["--host-id", host]

    fh = open(log_file, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
            start_new_session=False,
            env={**os.environ},
        )
    except OSError as e:
        fh.close()
        log(f"{USER_MSG_KEY} 启动失败 | {e}", "ERROR")
        return

    _children[USER_MSG_KEY] = {
        "popen": proc, "fh": fh, "started_at": time.time(), "backoff": backoff,
    }
    log(f"{USER_MSG_KEY} 已启动 | pid={proc.pid} | {script.name} --watch "
        f"--interval {int(USER_MSG_INTERVAL)} | host={host or '-'}")


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
    """Reconcile against config: start added, stop removed, leave untouched."""
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
    """Single-instance lock: two supervisors mean two sets of collectors fighting over the data."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
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
    # * must be declared inside the function: main() assigns `_reload = False`,
    #   and without a global declaration Python treats _reload as a local -
    #   the loop's first read then raises UnboundLocalError (measured: it crashed on startup).
    global _stopping, _reload

    lock = _acquire_lock()
    if lock is None:
        log("已有 supervisor 在运行（拿到锁失败），本进程退出", "WARN")
        return 0

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _on_signal)

    entries = _desired()
    if not entries:
        log("本机未配置 gateways / gateway，采集器不启动", "WARN")
        return 0

    log(f"启动 | pid={os.getpid()} | 子进程 {len(entries)} 个: "
        f"{[e['host_id'] for e in entries]}")
    _sweep_orphans([e["host_id"] for e in entries if e.get("kind", "relay") == "relay"])
    _reconcile(entries)

    try:
        while not _stopping:
            if _reload:
                _reload = False
                fresh = _desired()
                log(f"收到 SIGHUP，按新配置对账 | {len(fresh)} 个子进程")
                _reconcile(fresh)

            now = time.time()

            # (1) reap exited children (popen may already be None, so check first)
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
                item = next((e for e in _desired() if e["host_id"] == hid), None)
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
