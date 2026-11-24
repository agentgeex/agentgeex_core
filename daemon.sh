#!/usr/bin/env bash
# ============================================================================
# daemon.sh — AgentGeeX daemon start/stop/status/restart/log management script
#
# Usage:
#   ./daemon.sh start           # start the daemon (core mode by default)
#   ./daemon.sh start --host    # start in host mode (daemon + ws only, no watchdog)
#   ./daemon.sh stop            # graceful stop
#   ./daemon.sh stop --force    # force stop
#   ./daemon.sh status          # show running status
#   ./daemon.sh restart         # restart (core mode by default)
#   ./daemon.sh restart --host  # restart in host mode
#   ./daemon.sh logs            # tail the log
#
# Process management: PID file + process scan (singleton guard against concurrent instances)
# Log file: logs/{HOST_ID}.out
# Config file: agx_config.json
#
# Singleton guard:
#   Before start/stop we trust a process scan (pgrep), not the pidfile alone.
#   A pidfile can be stale, deleted, or point at a dead wrapper process; looking only at
#   alone would misjudge "not running" and start a second instance -> double daemon -> double watchdog -> repeated receipt delivery.
# ============================================================================

set -euo pipefail

# ── paths ──────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$PROJECT_ROOT/scripts/agx_daemon.py"

# ── config file location (2026-11-13) ─────────────────
# The config holds gateway tokens and other credentials and belongs to the runtime; the repo no longer carries a real config file.
# Order: $AGX_CONFIG_PATH -> this directory -> ~/.agx (the runtime home)
# The last one lets daemon.sh find the config even when started from the repo directory.
if [ -n "${AGX_CONFIG_PATH:-}" ] && [ -f "$AGX_CONFIG_PATH" ]; then
  CONFIG_FILE="$AGX_CONFIG_PATH"
elif [ -f "$PROJECT_ROOT/agx_config.json" ]; then
  CONFIG_FILE="$PROJECT_ROOT/agx_config.json"
elif [ -f "$HOME/.agx/agx_config.json" ]; then
  CONFIG_FILE="$HOME/.agx/agx_config.json"
else
  CONFIG_FILE="$PROJECT_ROOT/agx_config.json"   # keep the value as-is; a downstream step reports the error
fi

LOGS_DIR="$PROJECT_ROOT/logs"

# ── interpreter detection (2026-10-25) ──────────────────
# The old implementation hard-coded `uv run python3`, which hangs on 118: uv tries to
# download CPython from GitHub (TLS blocked), leaving only the uv wrapper, a 0-byte log, with no timeout ever firing.
# Two more traps:
#   · uv is installed but not on the non-interactive PATH (~/.local/bin/uv exists, command -v misses it)
#   · the system python3 is too old (118 has 3.8.10; the code uses `str | None`, needing 3.10+)
# We now probe in priority order and take the first usable one:
#   1. explicitly set $AGX_PYTHON
#   2. the project's own .venv (version-controlled, most reliable)
#   3. $AGX_SOURCE_DIR/.venv (when ~/.agx and the source are deployed separately)
#   4. uv run (uv available but no local venv)
#   5. system python3 (only usable when the version is >= 3.10)
AGX_MIN_MAJOR=3
AGX_MIN_MINOR=10
# Under set -u an undefined variable aborts, so give an explicit empty default
AGX_PYTHON="${AGX_PYTHON:-}"
AGX_SOURCE_DIR="${AGX_SOURCE_DIR:-}"

_py_ok() {
    local exe="$1"
    [ -x "$exe" ] || return 1
    "$exe" -c "import sys; sys.exit(0 if sys.version_info >= ($AGX_MIN_MAJOR, $AGX_MIN_MINOR) else 1)" 2>/dev/null
}

resolve_python() {
    if [ -n "$AGX_PYTHON" ]; then
        if _py_ok "$AGX_PYTHON"; then echo "$AGX_PYTHON"; return 0; fi
        warn "AGX_PYTHON=$AGX_PYTHON 不可用或版本 < ${AGX_MIN_MAJOR}.${AGX_MIN_MINOR}，继续探测" >&2
    fi

    local cand
    for cand in "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/.venv/bin/python3"; do
        if _py_ok "$cand"; then echo "$cand"; return 0; fi
    done

    if [ -n "$AGX_SOURCE_DIR" ]; then
        for cand in "$AGX_SOURCE_DIR/.venv/bin/python" "$AGX_SOURCE_DIR/.venv/bin/python3"; do
            if _py_ok "$cand"; then echo "$cand"; return 0; fi
        done
    fi

    # 3b. auto-detect the common source directory on this machine (~/.agx split deployment)
    #     * Why this must come before uv: measured on 118, `uv run` downloads CPython from
    #     GitHub; TLS is blocked -> it hangs with no output (only the uv wrapper is left,
    #     a 0-byte log, never timing out). A local source venv has a controlled version and needs no network, so it always beats uv run.
    local auto_src
    for auto_src in \
        "$HOME/project/ai_project/agentgeex/agentgeex" \
        "$HOME/project/agentgeex/agentgeex" \
          "$HOME/agentgeex/agentgeex" ; do
        for cand in "$auto_src/.venv/bin/python" "$auto_src/.venv/bin/python3"; do
            if _py_ok "$cand"; then
                echo "  (自动探测到源码 venv: $auto_src)" >&2
                echo "$cand"; return 0
            fi
        done
    done

    local uv_bin=""
    if command -v uv >/dev/null 2>&1; then
        uv_bin="$(command -v uv)"
    elif [ -x "$HOME/.local/bin/uv" ]; then
        uv_bin="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        uv_bin="$HOME/.cargo/bin/uv"
    fi
    if [ -n "$uv_bin" ]; then echo "$uv_bin run python3"; return 0; fi

    for cand in python3 python3.13 python3.12 python3.11 python3.10; do
        local p
        p="$(command -v "$cand" 2>/dev/null)" || continue
        if _py_ok "$p"; then echo "$p"; return 0; fi
    done

    return 1
}


# ── colors ─────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✅ $*${NC}"; }
warn(){ echo -e "${YELLOW}⚠️  $*${NC}"; }
err() { echo -e "${RED}❌ $*${NC}"; }

# ── run detached from the process group (macOS has no setsid; python os.setsid is equivalent) ──
# nohup blocks SIGHUP only; it does not move the process group. If the daemon lands inside
# the process group of the openclaw gateway, the gateway's group kill(0,SIGTERM) takes the
# daemon down with it. Happened three times: 2026-10-16 16:16, 2026-10-17 17:49, 2026-10-18 21:49 (the last stayed silent for 27h22m).
# A new session has no controlling terminal, so SIGHUP no longer needs nohup; start therefore does not use nohup any more.
# If the current process is already a session leader, os.setsid() raises EPERM; ignore and exec.
setsid_exec() {
    python3 -c '
import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])
' "$@"
}

mkdir -p "$LOGS_DIR"

# ── check the config file ──────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
    err "找不到配置文件: $CONFIG_FILE"
    exit 1
fi

# ── read the config with Python ────────────────────────
read_json() {
    python3 -c "
import json, sys
c = json.load(open(sys.argv[1]))
for k in sys.argv[2].split('.'):
    c = c[k]
print(c)
" "$CONFIG_FILE" "$1"
}

HOST_ID="${AGX_DAEMON_HOST_ID:-$(read_json 'host_id' 2>/dev/null || echo '')}"
NETWORK_ID="${AGX_DAEMON_NETWORK_ID:-$(read_json 'network_id' 2>/dev/null || echo '')}"
TEST_MODE=$(read_json 'daemon.watchdog_test_mode' 2>/dev/null || echo "0")

if [ -z "$HOST_ID" ]; then
    err "agx_config.json 中未设置 host_id，且未设置 AGX_DAEMON_HOST_ID 环境变量"
    exit 1
fi
if [ -z "$NETWORK_ID" ]; then
    err "agx_config.json 中未设置 network_id，且未设置 AGX_DAEMON_NETWORK_ID 环境变量"
    exit 1
fi

PID_FILE="$LOGS_DIR/${HOST_ID}.pid"
LOG_FILE="$LOGS_DIR/${HOST_ID}.out"

# ── parse arguments ────────────────────────────────────
parse_mode() {
    DAEMON_MODE="--core"
    FORCE_STOP=false
    for arg in "$@"; do
        case "$arg" in
            --core)  DAEMON_MODE="--core" ;;
            --host)  DAEMON_MODE="--host" ;;
            --force) FORCE_STOP=true ;;
            *)       warn "未知参数: $arg" ;;
        esac
    done
}

# ── resolve the PID ────────────────────────────────────
get_pid() {
    [ -f "$PID_FILE" ] && cat "$PID_FILE" || echo ""
}

is_pid_alive() {
    local pid="$1"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# ── process scan: fallback that does not trust the pidfile ──
# matches the uv wrapper process and the python3 it spawns (both cmdlines contain the script path)
# ★ 2026-11-24: must be anchored to THIS install root. A bare "scripts/agx_daemon.py" matches
#   every AgentGeeX instance on the machine, so `daemon.sh stop` on a second install
#   killed the first one's daemon (measured on 118: installing a demo tree under
#   ~/.agx_demo took down the ~/.agx instance). Absolute path keeps instances apart.
DAEMON_PATTERN="${PROJECT_ROOT}/scripts/agx_daemon\.py"

# every matching process (wrapper + real body) — used by stop
find_daemon_pids() {
    pgrep -f "$DAEMON_PATTERN" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//' || true
}

# instance-tree roots (whose parent is not in the match set) — used for counting and display
# the uv wrapper and the python3 body of one tree count as a single instance
find_daemon_roots() {
    local all p ppid out=""
    all="$(find_daemon_pids)"
    [ -z "$all" ] && return 0
    for p in $all; do
        ppid="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
        case " $all " in
            *" $ppid "*) ;;          # the parent is in the match set too -> inside a tree, skip
            *) out="$out $p" ;;       # the parent is not in the match set -> this is a tree root
        esac
    done
    echo "${out# }"
}

# actual instance count
count_daemon_instances() {
    local roots
    roots="$(find_daemon_roots)"
    [ -z "$roots" ] && { echo 0; return 0; }
    echo $roots | wc -w | tr -d ' '
}

# ── supervisor detection (Linux/systemd only; macOS never enters this path) ──
# * 2026-10-29: on Linux hosts (test_srv_a / test_srv_b) the daemon is managed by the
#   systemd **user unit** agx-daemon.service (ExecStart=daemon.sh run --core,
#   Restart=always + RestartSec=15). In that case a manual start always creates a double
#   instance: ensure_singleton kills the systemd copy -> systemd relaunches it 15s later
#   via Restart=always -> the machine ends up with 2 daemons / 2 watchdogs / 2 ws_clients.
#   Measured consequence: two watchdogs compete for the same executions, and the
#   read-modify-write of wake_count has no mutex -> every wake tier sends two wakes, leaving two different event_ids for one tier in the event table.
#
#   Isolation requirement (Lao Feng, 2026-10-29): Linux only; the fix must not touch macOS.
#   Hence three gates: not Linux -> return 1; no systemctl -> return 1;
#   unit not active -> return 1 too. On macOS the first gate exits and nothing else runs.
SYSTEMD_UNIT="agx-daemon"

is_systemd_managed() {
    [ "$(uname -s 2>/dev/null)" = "Linux" ] || return 1
    command -v systemctl >/dev/null 2>&1 || return 1
    # Without a user session (XDG_RUNTIME_DIR missing) systemctl --user fails ->
    # failure counts as "not managed" and we fall back to the manual singleton logic (fail-open, never blocks start).
    systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1 || return 1
    return 0
}

# when managed, print guidance only (take no action)
_systemd_hint() {
    err "本机 $SYSTEMD_UNIT 由 systemd 用户单元托管，不能用 daemon.sh $* 手工操作"
    echo "  原因: 手工启停或 ensure_singleton 会与 systemd 的 Restart=always 撞车，"
    echo "        导致一台机器上出现两个 daemon / 两个 watchdog。"
    echo ""
    echo "  请改用:"
    echo "    systemctl --user restart $SYSTEMD_UNIT   # 重启"
    echo "    systemctl --user stop    $SYSTEMD_UNIT   # 停止"
    echo "    systemctl --user status  $SYSTEMD_UNIT   # 状态"
    echo "    journalctl --user -u $SYSTEMD_UNIT -f    # 日志"
}

# ── singleton guard: clear every daemon of the same kind on the machine ──
# Whether or not the pidfile is trustworthy, clear existing processes before starting, guaranteeing a single global instance.
ensure_singleton() {
    local pids
    pids="$(find_daemon_pids)"
    if [ -z "$pids" ]; then
        rm -f "$PID_FILE"
        return 0
    fi

    warn "检测到已存在的守护进程: $pids — 先停止以保证单例"
    kill -TERM $pids 2>/dev/null || true

    local i
    for i in $(seq 1 10); do
        if [ -z "$(find_daemon_pids)" ]; then break; fi
        sleep 1
    done

    pids="$(find_daemon_pids)"
    if [ -n "$pids" ]; then
        warn "优雅退出超时，强制终止: $pids"
        kill -9 $pids 2>/dev/null || true
        sleep 1
    fi

    rm -f "$PID_FILE"
    ok "已清理旧守护进程，准备启动新实例"
}

# ── status: show running status ────────────────────────
status() {
    local pid pids roots count
    pid="$(get_pid)"
    pids="$(find_daemon_pids)"

    if [ -z "$pids" ]; then
        if [ -n "$pid" ]; then
            warn "PID 文件存在 ($pid) 但进程未运行 — pidfile 已过期"
        else
            warn "守护进程未运行"
        fi
        return 1
    fi

    roots="$(find_daemon_roots)"
    count="$(count_daemon_instances)"

    # multiple instances = abnormal, must warn (this is the root cause of duplicate receipts)
    if [ "$count" -gt 1 ]; then
        warn "检测到 $count 个守护进程实例（多实例异常）: $roots"
    fi

    local mode="未知"
    if [ -f "$LOGS_DIR/.watchdog_heartbeat" ]; then
        local hb_age
        # * cross-platform mtime: Linux uses stat -c %Y, macOS uses stat -f %m.
        #   The reverse order bites — on Linux `stat -f` means "inspect the file system", prints
        #   block info and makes the arithmetic expansion fail with `File: unbound variable` (which aborts under set -u).
        local hb_mtime
        hb_mtime=$(stat -c %Y "$LOGS_DIR/.watchdog_heartbeat" 2>/dev/null \
                   || stat -f %m "$LOGS_DIR/.watchdog_heartbeat" 2>/dev/null \
                   || echo 0)
        hb_age=$(($(date +%s) - hb_mtime))
        mode="core（看门狗心跳: ${hb_age}s 前）"
    else
        mode="host（无看门狗）"
    fi
    ok "守护进程运行中 (实例数 $count | PID $pids)"
    echo "   模式: $mode"
    echo "   日志: $LOG_FILE"
    if [ -n "$pid" ]; then
        echo "   PID 文件: $PID_FILE → $pid"
    fi
    return 0
}

# ── start: launch the daemon ───────────────────────────
start() {
    parse_mode "$@"

    # * 2026-10-29: on managed hosts reject a manual start (this must come **before**
    #   ensure_singleton — otherwise the systemd instance is killed first, then 15s later we report 'do not start', which is a pointless outage).
    #   macOS: is_systemd_managed returns 1 on its first line, so this branch is never entered.
    if is_systemd_managed; then
        _systemd_hint start
        exit 1
    fi

    # singleton guard: clear every existing instance (including orphans missed/stale in the pidfile)
    ensure_singleton

    local hostname_short
    hostname_short=$(hostname -s)

    echo "本机 hostname: $hostname_short"
    echo "network_id:    $NETWORK_ID"
    echo "host_id:       $HOST_ID"
    echo "测试模式:      $([ "$TEST_MODE" = "1" ] && echo '✅ 开启 (只记 Redis，不分派)' || echo '❌ 关闭 (正常运行)')"
    echo "配置文件:      $CONFIG_FILE"
    echo ""
    # resolve the interpreter (2026-10-25: no longer hard-coded uv run, see "interpreter detection")
    local PY_CMD
    if ! PY_CMD="$(resolve_python)"; then
        err "找不到可用解释器（需要 Python >= ${AGX_MIN_MAJOR}.${AGX_MIN_MINOR}）"
        echo "        已尝试: \$AGX_PYTHON → $PROJECT_ROOT/.venv → \$AGX_SOURCE_DIR/.venv → uv → 系统 python3"
        echo "        修复: 在 $PROJECT_ROOT 下建 .venv，或设 AGX_PYTHON=/path/to/python3.12"
        echo "              或设 AGX_SOURCE_DIR=/path/to/agentgeex（使用源码 venv）"
        exit 1
    fi

    echo "启动守护进程..."
    echo "  运行模式:     ${DAEMON_MODE#--}"
    echo "  解释器:       $PY_CMD"
    echo "  脚本:         $SCRIPT"
    echo "  项目:         $PROJECT_ROOT"

    cd "$PROJECT_ROOT"

    # shellcheck disable=SC2086  # PY_CMD may be "uv run python3" (contains a space); splitting is intentional
    setsid_exec $PY_CMD "$SCRIPT" $DAEMON_MODE >> "$LOG_FILE" 2>&1 &
    local pid=$!

    # brief wait, then confirm the process is alive
    sleep 2
    if is_pid_alive "$pid"; then
        echo "$pid" > "$PID_FILE"
        echo ""
        ok "守护进程启动成功 (PID $pid)"
        echo "   进程组:       $(ps -o pgid= -p $pid 2>/dev/null | tr -d ' ') (独立组，gateway 重启不连坐)"
        echo "   日志文件: $LOG_FILE"
        echo "   停止: ./daemon.sh stop"
        echo "   日志: ./daemon.sh logs"
    else
        err "守护进程启动失败"
        echo "        请检查日志: $LOG_FILE"
        tail -20 "$LOG_FILE" 2>/dev/null || true
        exit 1
    fi
}

# ── stop: stop the daemon ──────────────────────────────
stop() {
    parse_mode "$@"

    local pid pids
    pid="$(get_pid)"
    pids="$(find_daemon_pids)"

    # merge in PIDs from the pidfile that are still alive (in case the scan missed them)
    if [ -n "$pid" ] && is_pid_alive "$pid"; then
        if [ -z "$pids" ]; then
            pids="$pid"
        elif [ "${pids#*$pid}" = "$pids" ]; then
            pids="$pids $pid"
        fi
    fi

    if [ -z "$pids" ]; then
        warn "守护进程未运行"
        rm -f "$PID_FILE"
        return 0
    fi

    # show current status
    status || true
    echo ""

    if [ "$FORCE_STOP" = true ]; then
        echo "强制终止 ($pids)..."
        kill -9 $pids 2>/dev/null || true
        ok "已强制终止"
    else
        echo "优雅停止 ($pids)..."
        echo "  发送 SIGTERM..."
        kill -TERM $pids 2>/dev/null || true

        # wait for the process to exit (10s max)
        echo "  等待退出（最多 10 秒）..."
        local i
        for i in $(seq 1 10); do
            if [ -z "$(find_daemon_pids)" ]; then break; fi
            sleep 1
        done

        local left
        left="$(find_daemon_pids)"
        if [ -n "$left" ]; then
            warn "超时未退出，强制终止: $left"
            kill -9 $left 2>/dev/null || true
            ok "已强制终止"
        else
            ok "守护进程已优雅退出"
        fi
    fi

    rm -f "$PID_FILE"
    echo ""
    echo "查看最后几条日志: tail -20 $LOG_FILE"
}

# ── logs: tail the log ─────────────────────────────────
logs() {
    exec tail -F "$LOG_FILE"
}

# ── run: run in the foreground (used by systemd, 2026-10-25) ──
# Unlike start: no setsid detach, no log redirection, just exec the main process.
# That way systemd (Type=simple) tracks the real main PID — process death is service
# death, which is the only way Restart=always is meaningful.
# Why start cannot be paired with oneshot: start is a script that exits once started,
# so systemd records the script's exit code and never learns that the main process
# died later (measured: after killing the main process, is-active is still active but only 2 child processes are left).
run_foreground() {
    local PY_CMD
    if ! PY_CMD="$(resolve_python)"; then
        err "找不到可用解释器（需要 Python >= ${AGX_MIN_MAJOR}.${AGX_MIN_MINOR}）"
        exit 1
    fi
    cd "$PROJECT_ROOT"
    echo "前台运行 | host_id=$HOST_ID | 解释器=$PY_CMD | 模式=${DAEMON_MODE#--}"
    # shellcheck disable=SC2086
    exec $PY_CMD "$SCRIPT" $DAEMON_MODE
}

# ── main dispatch ──────────────────────────────────────
case "${1:-}" in
    start)   shift; start "$@" ;;
    # * 2026-10-29: on managed hosts restart delegates straight to systemd, not stop+start.
    #   Why not stop;start: stop kills the service, start then refuses to run, so the
    #   service only comes back after systemd's 15s Restart=always — one restart that manufactures a fresh outage.
    #   No recursion risk: systemd calls run (ExecStart) and stop (ExecStop); neither passes through this branch.
    restart)
        shift
        if is_systemd_managed; then
            echo "检测到 systemd 用户单元托管 → 委派 systemctl --user restart $SYSTEMD_UNIT"
            systemctl --user restart "$SYSTEMD_UNIT"
            rc=$?
            sleep 2
            if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
                echo "✅ $SYSTEMD_UNIT 已重启（systemd 管理，单实例由 systemd 保证）"
            else
                echo "❌ $SYSTEMD_UNIT 重启后未处于 active"
                exit 1
            fi
            exit $rc
        fi
        stop; sleep 1; start "$@"
        ;;
    stop)
        shift
        stop "$@"
        # * 2026-10-29: a manual stop on a managed host gets relaunched by Restart=always —
        #   we only warn and do not change behaviour (delegating to systemctl stop here would recurse: the unit's ExecStop is daemon.sh stop itself).
        if is_systemd_managed; then
            echo ""
            err "本机由 systemd 托管：本次 stop 会在 RestartSec=15 后被 systemd 自动拉起"
            echo "  真正停服请用: systemctl --user stop $SYSTEMD_UNIT"
        fi
        ;;
    status)  shift; status ;;
    logs)    shift; logs ;;
    run)     shift; parse_mode "$@"; run_foreground ;;
    *)
        echo "用法: $0 {start [--core|--host]|stop [--force]|status|restart [--core|--host]|logs|run [--core|--host]}"
        exit 1
        ;;
esac
