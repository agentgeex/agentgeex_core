#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Win3 MCP SSE instance - resident start/stop script (* LEGACY since 2026-11-04)
#
# WARNING: no longer the BFF data channel. The BFF now connects to the shared gateway
#    http://127.0.0.1:31890/agentgeex/mcp（PM2 id 7 / agx-mcp-gateway，
#    (streamable-http). This script is kept only for two cases:
#      (1) rollback: WEBTOOLS_LEGACY_SSE=1 ./start.sh start
#      (2) port 31888 may still have **external clients** attached (a real case: /Applications/xxyun.app),
#          and stopping it would break them -- so start/stop is kept, but start.sh no longer launches it by default.
#
# [2026-10-05 Feng decision: the BFF speaks the MCP protocol (SSE), fully same-origin with MCP]
# The BFF no longer imports the kernel in-process; it calls mcp_server
# tool functions through this SSE instance (JSON-RPC). This script keeps that instance resident (it is not registered with pm2 -- a client machine may not have it).
#
# Port source: mcp.sse_host / mcp.sse_port in the main config agx_config.json
#          (default 127.0.0.1:31888; MCP_SSE_PORT overrides temporarily)
#
# Usage:
#   ./run_mcp_sse.sh start     # start (idempotent: skipped when already running)
#   ./run_mcp_sse.sh stop      # stop
#   ./run_mcp_sse.sh restart   # restart
#   ./run_mcp_sse.sh status    # show status
#   ./run_mcp_sse.sh logs      # tail the log
# ─────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGX_ROOT="$(cd "$HERE/../.." && pwd)"
PY="$AGX_ROOT/.venv/bin/python"

PIDFILE="/tmp/win3_mcp_sse.pid"
LOGFILE="/tmp/win3_mcp_sse.log"

# Read the port from the main config (the same source the MCP instance reads; single source of truth)
read_host_port() {
  "$PY" - <<'PYEOF'
import json, os, pathlib
root = pathlib.Path(os.environ["AGX_ROOT"])
host, port = "127.0.0.1", 31888
p = root / "agx_config.json"
try:
    cfg = json.load(open(p, encoding="utf-8"))
    m = cfg.get("mcp") or {}
    host = str(m.get("sse_host", host) or host)
    port = int(m.get("sse_port", port) or port)
except Exception:
    pass
host = os.environ.get("MCP_SSE_HOST", host)
port = os.environ.get("MCP_SSE_PORT", port)
print(f"{host} {port}")
PYEOF
}

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-start}" in
  start)
    if is_running; then
      echo "MCP SSE 已在运行 (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    read -r HOST PORT <<< "$(AGX_ROOT="$AGX_ROOT" read_host_port)"
    echo "启动 MCP SSE @ http://$HOST:$PORT/sse ..."
    cd "$AGX_ROOT"
    # WARNING: mcp_server.py has a built-in watch_stdin_sync: on macOS it treats a change in os.getppid() as
    #    "the parent died", so any parent change (e.g. the shell exiting after nohup and orphaning us) makes it self-kill.
    #    So a **double fork** daemonisation is mandatory: keep the parent PID stable at init(1) to avoid being killed by mistake.
    #    Also: clear the proxy (this is a local SSE instance; do not send it through clash).
    env -u HTTPS_PROXY -u HTTP_PROXY -u HTTP_PROXY -u ALL_PROXY \
      "$PY" -c '
import os, sys, time, subprocess
pidfile, logfile, root = sys.argv[1], sys.argv[2], sys.argv[3]
# first fork
if os.fork() > 0:
    os._exit(0)
os.setsid()
# second fork (fully detached, ppid -> init)
if os.fork() > 0:
    os._exit(0)
os.chdir(root)
logf = open(logfile, "ab", buffering=0)
os.dup2(logf.fileno(), 1)
os.dup2(logf.fileno(), 2)
# record the real pid
with open(pidfile, "w") as f:
    f.write(str(os.getpid()))
p = subprocess.Popen(
    [sys.executable, "agx_mcp/mcp_server.py", "--transport=sse"],
    stdin=subprocess.DEVNULL,
    stdout=logf, stderr=logf,
)
# daemon: write the child pid back (for status/stop) and forward the exit
with open(pidfile, "w") as f:
    f.write(str(p.pid))
p.wait()
' "$PIDFILE" "$LOGFILE" "$AGX_ROOT"
    sleep 8
    if is_running; then
      echo "✅ 已启动 (pid $(cat "$PIDFILE"))，日志: $LOGFILE"
    else
      echo "❌ 启动失败，见 $LOGFILE" >&2
      tail -20 "$LOGFILE" >&2 || true
      exit 1
    fi
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PIDFILE")" && echo "已停止 (pid $(cat "$PIDFILE"))"
      rm -f "$PIDFILE"
    else
      echo "未在运行"
    fi
    ;;
  restart)
    "$0" stop || true
    sleep 1
    "$0" start
    ;;
  status)
    if is_running; then
      echo "运行中 (pid $(cat "$PIDFILE"))"
    else
      echo "未运行"
    fi
    read -r HOST PORT <<< "$(AGX_ROOT="$AGX_ROOT" read_host_port)"
    lsof -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || echo "端口 $PORT 未监听"
    ;;
  logs)
    tail -f "$LOGFILE"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs}" >&2
    exit 1
    ;;
esac
