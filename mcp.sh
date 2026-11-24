#!/usr/bin/env bash
# ============================================================================
# mcp.sh — MCP gateway start/stop/status/restart/log management script
#
# Role: a **backup launch mode** alongside PM2.
#   Production uses PM2 (`pm2 list | grep agx-mcp-gateway`); this script is for:
#     · machines without PM2 / a broken PM2 / a PM2 that cannot keep it alive
#     · ad-hoc troubleshooting (run in the foreground, see the full output)
#     · systemd / launchd supervision (via the `run` subcommand)
#
# Usage:
#   ./mcp.sh start                  # start in the background (127.0.0.1:31890 by default)
#   ./mcp.sh start --port 31890 --host 127.0.0.1 --threads 120
#   ./mcp.sh start --mount agentgeex,redis  # mount a subset only
#   ./mcp.sh start --force          # ignore the "PM2 already supervises it" refusal (careful: double instance)
#   ./mcp.sh stop                   # graceful stop
#   ./mcp.sh stop --force           # force stop (SIGKILL)
#   ./mcp.sh restart                # restart
#   ./mcp.sh status                 # running state + port + endpoints
#   ./mcp.sh logs                   # tail the log
#   ./mcp.sh run                    # run in the foreground (systemd/launchd, or ad-hoc debugging)
#   ./mcp.sh check                  # endpoint self-check (one handshake per /xxx/mcp)
#
# Process management: PID file + process scan (singleton guard against PM2 coexistence creating doubles)
# Log file: logs/agx-mcp-gateway.out
# Port:     31890 (/agentgeex/mcp, /mysql/mcp, /redis/mcp)
#
# Singleton guard:
#   Before start/stop we trust a process scan (pgrep), not the pidfile alone.
#   A pidfile can be stale/deleted/point at a dead wrapper process; trusting it alone
#   misjudges "not running" and starts a second instance -> two processes fight over one port,
#   We also check PM2: while PM2 supervises it, a manual start always creates a double instance.
# ============================================================================

set -euo pipefail

# ── paths ─────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$PROJECT_ROOT/agx_mcp/mcp_gateway.py"
LOGS_DIR="$PROJECT_ROOT/logs"

DEFAULT_PORT=31890
DEFAULT_HOST=127.0.0.1
DEFAULT_THREADS=120

# ── interpreter detection (same strategy as daemon.sh; see the pitfalls documented there) ──
# Order: $AGX_PYTHON -> project .venv -> $AGX_SOURCE_DIR/.venv -> auto-detected source venv
#        -> uv run -> system python3 (>=3.10)
# MCP adds one requirement: fastmcp must be installable in the venv (it pulls uvicorn in).
AGX_MIN_MAJOR=3
AGX_MIN_MINOR=10
AGX_PYTHON="${AGX_PYTHON:-}"
AGX_SOURCE_DIR="${AGX_SOURCE_DIR:-}"

_py_ok() {
    local exe="$1"
    [ -x "$exe" ] || return 1
    "$exe" -c "import sys; sys.exit(0 if sys.version_info >= ($AGX_MIN_MAJOR, $AGX_MIN_MINOR) else 1)" 2>/dev/null
}

# MCP-specific: beyond the version, it must be able to import fastmcp, otherwise it crashes on start
_py_has_fastmcp() {
    local exe="$1"
    "$exe" -c "import fastmcp" >/dev/null 2>&1
}

resolve_python() {
    if [ -n "$AGX_PYTHON" ]; then
        if _py_ok "$AGX_PYTHON" && _py_has_fastmcp "$AGX_PYTHON"; then echo "$AGX_PYTHON"; return 0; fi
        warn "AGX_PYTHON=$AGX_PYTHON 不可用 / 版本过低 / 缺 fastmcp，继续探测" >&2
    fi

    local cand
    for cand in "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/.venv/bin/python3"; do
        if _py_ok "$cand" && _py_has_fastmcp "$cand"; then echo "$cand"; return 0; fi
    done

    if [ -n "$AGX_SOURCE_DIR" ]; then
        for cand in "$AGX_SOURCE_DIR/.venv/bin/python" "$AGX_SOURCE_DIR/.venv/bin/python3"; do
            if _py_ok "$cand" && _py_has_fastmcp "$cand"; then echo "$cand"; return 0; fi
        done
    fi

    local auto_src
    for auto_src in \
        "$HOME/project/ai_project/agentgeex/agentgeex" \
        "$HOME/project/ai_project/agentgeex/agentgeex" ; do
        for cand in "$auto_src/.venv/bin/python" "$auto_src/.venv/bin/python3"; do
            if _py_ok "$cand" && _py_has_fastmcp "$cand"; then
                echo "  (自动探测到源码 venv: $auto_src)" >&2
                echo "$cand"; return 0
            fi
        done
    done

    # uv run: uses the interpreter managed by uv, so fastmcp importability must be verified separately
    local uv_bin=""
    if command -v uv >/dev/null 2>&1; then
        uv_bin="$(command -v uv)"
    elif [ -x "$HOME/.local/bin/uv" ]; then
        uv_bin="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        uv_bin="$HOME/.cargo/bin/uv"
    fi
    if [ -n "$uv_bin" ]; then
        if (cd "$PROJECT_ROOT" && "$uv_bin" run python3 -c "import fastmcp" >/dev/null 2>&1); then
            echo "$uv_bin run python3"; return 0
        fi
    fi

    for cand in python3 python3.13 python3.12 python3.11 python3.10; do
        local p
        p="$(command -v "$cand" 2>/dev/null)" || continue
        if _py_ok "$p" && _py_has_fastmcp "$p"; then echo "$p"; return 0; fi
    done

    return 1
}

# ── colors ─────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✅ $*${NC}"; }
warn(){ echo -e "${YELLOW}⚠️  $*${NC}"; }
err() { echo -e "${RED}❌ $*${NC}"; }

# ── detach from the process group (macOS has no setsid; python os.setsid is equivalent) ──
# If it lands inside the openclaw gateway's process group, the gateway's group teardown kills the MCP service too.
# daemon.sh already lost its service three times to this pitfall (2026-10-16/17/18); the same approach is copied here.
#
# * deliberately NOT wrapped in a function: `setsid_exec ... &` forks an extra bash subshell to run
#   the function, so `$!` is that subshell's pid (not the real process) -> the pidfile records the
#   wrong pid, and that bash stays around waiting for python. Hence python3 -c is inlined in start:
#   an external command with in-place execvp replacement, so `$!` is exactly the real pid, no wrapper.
SETSID_SNIPPET=$(cat <<'PYCODE'
import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])
PYCODE
)

mkdir -p "$LOGS_DIR"

# ── runtime arguments (may be overridden by start; stop/status also need to know the port) ──
PORT="${AGX_MCP_PORT:-$DEFAULT_PORT}"
HOST="${AGX_MCP_HOST:-$DEFAULT_HOST}"
THREADS="${AGX_MCP_THREADS:-$DEFAULT_THREADS}"
MOUNT="${AGX_MCP_MOUNT:-}"
FORCE=false

PID_FILE="$LOGS_DIR/agx-mcp-gateway.pid"
LOG_FILE="$LOGS_DIR/agx-mcp-gateway.out"
PM2_NAME="agx-mcp-gateway"

# ── argument parsing ───────────────────────────────────
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --port)    PORT="${2:-}"; shift 2 ;;
            --port=*)  PORT="${1#*=}"; shift ;;
            --host)    HOST="${2:-}"; shift 2 ;;
            --host=*)  HOST="${1#*=}"; shift ;;
            --threads) THREADS="${2:-}"; shift 2 ;;
            --threads=*) THREADS="${1#*=}"; shift ;;
            --mount)   MOUNT="${2:-}"; shift 2 ;;
            --mount=*) MOUNT="${1#*=}"; shift ;;
            --force)   FORCE=true; shift ;;
            *)         warn "未知参数: $1"; shift ;;
        esac
    done
}

# ── PID / process scan ────────────────────────────────
get_pid() {
    [ -f "$PID_FILE" ] && cat "$PID_FILE" || echo ""
}

is_pid_alive() {
    local pid="$1"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# match only mcp_gateway.py, not the three service files (those are libraries, not entry points)
MCP_PATTERN="agx_mcp/mcp_gateway\.py"

find_mcp_pids() {
    pgrep -f "$MCP_PATTERN" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//' || true
}

# instance-tree roots (whose parent is not in the match set). A wrapper and its real body count as one instance.
find_mcp_roots() {
    local all p ppid out=""
    all="$(find_mcp_pids)"
    [ -z "$all" ] && return 0
    for p in $all; do
        ppid="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
        case " $all " in
            *" $ppid "*) ;;
            *) out="$out $p" ;;
        esac
    done
    echo "${out# }"
}

count_mcp_instances() {
    local roots
    roots="$(find_mcp_roots)"
    [ -z "$roots" ] && { echo 0; return 0; }
    echo $roots | wc -w | tr -d ' '
}

port_owner() {
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

# ── PM2 supervision detection ─────────────────────────
# PM2 online plus a manual start = double instance (PM2 relaunches its own via autorestart).
# Three cases: pm2 missing / present but the app is not listed / listed and online.
# * parse jlist with python, never grep.
#   Known pitfall: `grep -o '"name":"X"[^}]*}'` truncates at the first `}`
#   while status lives further down in pm2_env -> never matches -> online is misread as stopped
#   -> start does not refuse -> the manual instance and the PM2 instance fight over one port.
pm2_state() {
    command -v pm2 >/dev/null 2>&1 || { echo "absent"; return 0; }
    local js out
    js="$(pm2 jlist 2>/dev/null || echo '[]')"
    if command -v python3 >/dev/null 2>&1; then
        out="$(printf '%s' "$js" | python3 -c '
import json, sys
name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print("unmanaged"); raise SystemExit
for p in data:
    if p.get("name") == name:
        st = (p.get("pm2_env") or {}).get("status", "")
        print("online" if st == "online" else "stopped")
        break
else:
    print("unmanaged")
' "$PM2_NAME" 2>/dev/null)"
        [ -n "$out" ] && { echo "$out"; return 0; }
    fi
    # Without python3 fall back to grep (lenient: better to falsely report online and let start refuse)
    if printf '%s' "$js" | grep -q "\"name\":\"$PM2_NAME\""; then
        echo "stopped"; else echo "unmanaged"
    fi
}

_pm2_hint() {
    err "PM2 正在托管 ${PM2_NAME}，不能手工 $* （会造双实例）"
    echo ""
    echo "  请改用:"
    echo "    pm2 restart $PM2_NAME     # 重启（改代码后用这个）"
    echo "    pm2 stop    $PM2_NAME     # 停止"
    echo "    pm2 logs    $PM2_NAME     # 日志"
    echo ""
    echo "  确实要用本脚本（PM2 已坏/要脱离 PM2）:"
    echo "    pm2 delete $PM2_NAME && ./mcp.sh start"
    echo "  或强行并存（不推荐，会抢端口）: ./mcp.sh start --force"
}

# ── singleton guard ───────────────────────────────────
ensure_singleton() {
    local pids
    pids="$(find_mcp_pids)"
    if [ -z "$pids" ]; then
        rm -f "$PID_FILE"
        return 0
    fi

    warn "检测到已存在的 MCP 网关: $pids — 先停止以保证单例"
    kill -TERM $pids 2>/dev/null || true

    local i
    for i in $(seq 1 10); do
        [ -z "$(find_mcp_pids)" ] && break
        sleep 1
    done

    pids="$(find_mcp_pids)"
    if [ -n "$pids" ]; then
        warn "优雅退出超时，强制终止: $pids"
        kill -9 $pids 2>/dev/null || true
        sleep 1
    fi

    rm -f "$PID_FILE"
    ok "已清理旧实例，准备启动"
}

# ── check: endpoint self-check ────────────────────────
check() {
    local py
    if ! py="$(resolve_python)"; then
        err "找不到可用解释器，无法自检"
        return 1
    fi

    local paths="$MOUNT"
    [ -z "$paths" ] && paths="agentgeex,mysql,redis"

    cd "$PROJECT_ROOT"
    # shellcheck disable=SC2086
    NO_PROXY=127.0.0.1,localhost $py - "$HOST" "$PORT" "$paths" <<'PY'
import json, sys, urllib.request

host, port, paths = sys.argv[1], sys.argv[2], sys.argv[3]
base = f"http://{host}:{port}"
body = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "mcp.sh", "version": "1"}},
}).encode()

bad = 0
for p in [x.strip() for x in paths.split(",") if x.strip()]:
    url = f"{base}/{p}/mcp"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            txt = r.read(600).decode("utf-8", "replace")
        good = r.status == 200 and '"result"' in txt
    except Exception as exc:
        good, txt = False, f"{type(exc).__name__}: {exc}"
    print(f"  {'✅' if good else '❌'} {url}  {'' if good else txt[:100]}")
    bad += 0 if good else 1
sys.exit(1 if bad else 0)
PY
}

# ── status ────────────────────────────────────────────
status() {
    local pid pids roots count owner st
    pid="$(get_pid)"
    pids="$(find_mcp_pids)"
    owner="$(port_owner)"
    st="$(pm2_state)"

    echo "── MCP 网关状态 ──────────────────────────────"
    echo "  端口:        $HOST:$PORT"
    case "$st" in
        online)    echo "  托管:        PM2（${PM2_NAME}，用 pm2 命令操作）" ;;
        stopped)   echo "  托管:        PM2 有该应用但已停止" ;;
        unmanaged) echo "  托管:        无 PM2 记录" ;;
        absent)    echo "  托管:        本机无 PM2" ;;
        *)         echo "  托管:        $st" ;;
    esac

    if [ -z "$pids" ]; then
        if [ -n "$owner" ]; then
            warn "端口 $PORT 被 pid $owner 占用，但不是 mcp_gateway.py（查: lsof -nP -iTCP:${PORT}）"
            return 1
        fi
        if [ -n "$pid" ]; then
            warn "未运行（PID 文件 $pid 已过期）"
        else
            warn "未运行"
        fi
        return 1
    fi

    roots="$(find_mcp_roots)"
    count="$(count_mcp_instances)"
    [ "$count" -gt 1 ] && warn "检测到 $count 个实例（异常，多实例会抢端口）: $roots"

    ok "运行中（实例数 $count | PID: ${pids}）"
    local p
    for p in $pids; do
        printf "    pid %-7s uptime=%-10s rss=%s\n" "$p" \
            "$(ps -o etime= -p "$p" 2>/dev/null | tr -d ' ')" \
            "$(ps -o rss= -p "$p" 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')"
    done
    echo "    监听:      ${owner:-<无>}"
    echo "    日志:      $LOG_FILE"
    [ -n "$pid" ] && echo "    PID 文件:  $PID_FILE → $pid"
    echo ""
    echo "── 端点 ──────────────────────────────────────"
    check || true
    return 0
}

# ── start ─────────────────────────────────────────────
start() {
    local st
    st="$(pm2_state)"
    if [ "$st" = "online" ] && [ "$FORCE" != true ]; then
        _pm2_hint start
        exit 1
    fi
    [ "$st" = "online" ] && warn "--force：PM2 仍在托管，可能出现双实例抢端口"

    ensure_singleton

    # port occupancy check (if a foreign process holds it, do not force a bind that would fail and restart-loop)
    local owner
    owner="$(port_owner)"
    if [ -n "$owner" ]; then
        err "端口 $PORT 已被 pid $owner 占用"
        echo "        查看: lsof -nP -iTCP:$PORT -sTCP:LISTEN"
        echo "        换端口: ./mcp.sh start --port 31891"
        exit 1
    fi

    local PY_CMD
    if ! PY_CMD="$(resolve_python)"; then
        err "找不到可用解释器（需 Python >= ${AGX_MIN_MAJOR}.${AGX_MIN_MINOR} 且能 import fastmcp）"
        echo "        已尝试: \$AGX_PYTHON → .venv → \$AGX_SOURCE_DIR/.venv → 自动探测 → uv → 系统 python3"
        echo "        修复: 在 $PROJECT_ROOT 下建 .venv 并 pip install -r requirements.txt"
        exit 1
    fi

    local args=(--host "$HOST" --port "$PORT" --max-threads "$THREADS")
    [ -n "$MOUNT" ] && args+=(--mount "$MOUNT")

    echo "启动 MCP 网关..."
    echo "  监听:     $HOST:$PORT"
    echo "  线程池:   $THREADS"
    echo "  挂载:     ${MOUNT:-全部（agentgeex,mysql,redis）}"
    echo "  解释器:   $PY_CMD"
    echo "  脚本:     $SCRIPT"

    cd "$PROJECT_ROOT"
    # shellcheck disable=SC2086
    # shellcheck disable=SC2086  # PY_CMD may be "uv run python3"; splitting is intentional
    python3 -c "$SETSID_SNIPPET" $PY_CMD "$SCRIPT" "${args[@]}" >> "$LOG_FILE" 2>&1 &
    local pid=$!

    sleep 3
    if ! is_pid_alive "$pid"; then
        err "启动失败（进程已退出）"
        echo "        日志: $LOG_FILE"
        tail -20 "$LOG_FILE" 2>/dev/null || true
        exit 1
    fi

    # did the port really come up (python may run but uvicorn failed to bind; the process lingers a few seconds)
    local i
    for i in $(seq 1 10); do
        [ -n "$(port_owner)" ] && break
        sleep 1
    done
    if [ -z "$(port_owner)" ]; then
        err "进程在跑但端口 $PORT 未监听"
        tail -20 "$LOG_FILE" 2>/dev/null || true
        exit 1
    fi

    # trust the process actually listening on the port (in case a wrapper forked instead of exec'ing, this covers it)
    local real_pid
    real_pid="$(port_owner)"
    [ -n "$real_pid" ] && pid="$real_pid"
    echo "$pid" > "$PID_FILE"
    echo ""
    ok "启动成功 (PID $pid)"
    echo "   进程组: $(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ') (独立组，gateway 重启不连坐)"
    echo "   停止:   ./mcp.sh stop"
    echo "   日志:   ./mcp.sh logs"
    echo ""
    check || warn "端点自检未全通过，看日志: tail -30 $LOG_FILE"
}

# ── stop ──────────────────────────────────────────────
stop() {
    local pid pids st
    # * refuse while PM2 is online: find_mcp_pids uses pgrep -f and also matches the PM2 copy,
    #   so killing it would take down the PM2-managed instance -> PM2 relaunches by autorestart -> port fight.
    st="$(pm2_state)"
    if [ "$st" = "online" ] && [ "$FORCE" != true ]; then
        _pm2_hint stop
        exit 1
    fi

    pid="$(get_pid)"
    pids="$(find_mcp_pids)"

    if [ -n "$pid" ] && is_pid_alive "$pid"; then
        case " $pids " in
            *" $pid "*) ;;
            *) pids="${pids:+$pids }$pid" ;;
        esac
    fi

    if [ -z "$pids" ]; then
        warn "MCP 网关未运行"
        rm -f "$PID_FILE"
        return 0
    fi

    echo "当前: $pids"
    if [ "$FORCE" = true ]; then
        echo "强制终止 ($pids)..."
        kill -9 $pids 2>/dev/null || true
        ok "已强制终止"
    else
        echo "优雅停止 ($pids)..."
        kill -TERM $pids 2>/dev/null || true
        local i
        for i in $(seq 1 10); do
            [ -z "$(find_mcp_pids)" ] && break
            sleep 1
        done
        local left
        left="$(find_mcp_pids)"
        if [ -n "$left" ]; then
            warn "超时未退出，强制终止: $left"
            kill -9 $left 2>/dev/null || true
            ok "已强制终止"
        else
            ok "已优雅退出"
        fi
    fi

    rm -f "$PID_FILE"
}

# ── logs ──────────────────────────────────────────────
logs() {
    exec tail -F "$LOG_FILE"
}

# ── run: foreground execution (systemd/launchd supervision, ad-hoc debugging) ──
# no setsid, no log redirection, just exec the main process — so systemd (Type=simple)
# tracks the real main PID: process death is service death, which is what makes Restart=always meaningful.
run_foreground() {
    local PY_CMD
    if ! PY_CMD="$(resolve_python)"; then
        err "找不到可用解释器（需 Python >= ${AGX_MIN_MAJOR}.${AGX_MIN_MINOR} 且能 import fastmcp）"
        exit 1
    fi
    cd "$PROJECT_ROOT"
    local args=(--host "$HOST" --port "$PORT" --max-threads "$THREADS")
    [ -n "$MOUNT" ] && args+=(--mount "$MOUNT")
    echo "前台运行 | $HOST:$PORT | 线程池=$THREADS | 挂载=${MOUNT:-全部} | 解释器=$PY_CMD"
    # shellcheck disable=SC2086
    exec $PY_CMD "$SCRIPT" "${args[@]}"
}

# ── main dispatch ─────────────────────────────────────
case "${1:-}" in
    start)   shift; parse_args "$@"; start ;;
    stop)    shift; parse_args "$@"; stop ;;
    restart)
        shift; parse_args "$@"
        local_st="$(pm2_state)"
        if [ "$local_st" = "online" ] && [ "$FORCE" != true ]; then
            _pm2_hint restart
            exit 1
        fi
        stop; sleep 1; start
        ;;
    status)  shift; parse_args "$@"; status ;;
    check)   shift; parse_args "$@"; check ;;
    logs)    shift; logs ;;
    run)     shift; parse_args "$@"; run_foreground ;;
    *)
        echo "用法: $0 {start|stop|restart|status|check|logs|run} [--port N] [--host IP] [--threads N] [--mount a,b] [--force]"
        echo ""
        echo "  start    后台启动（PM2 托管时拒绝，需 --force）"
        echo "  stop     优雅停止（--force 则 SIGKILL）"
        echo "  restart  重启"
        echo "  status   运行状态 + 端口 + 端点自检"
        echo "  check    仅端点自检"
        echo "  logs     滚动看日志"
        echo "  run      前台运行（systemd/launchd 用）"
        exit 1
        ;;
esac
