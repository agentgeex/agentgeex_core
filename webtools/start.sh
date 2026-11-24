#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Win3 商业控制台 — 一键启停脚本（编排三层）
#
#   ① MCP 共享网关 http://127.0.0.1:31890/agentgeex/mcp（BFF 的数据通道）
#      ★ 2026-11-04 产品负责人：MCP 公共服务统一。原先 BFF 自己起一个 SSE 实例(31888)，
#        现在改为连共享网关（PM2 id 7 / agx-mcp-gateway），本脚本**不再负责启动它**，
#        只做探活 + 给出启动提示（PM2 是它的唯一管理者，抢着起会撞端口）。
#   ② BFF (FastAPI) http://127.0.0.1:8090        （React 后端）
#   ③ 前端 (Vite)   http://localhost:5190        （商业控制台 UI）
#
# 用法：
#   ./start.sh start      # 启动全部（幂等：已在跑的跳过）
#   ./start.sh stop       # 停止全部
#   ./start.sh restart    # 重启全部
#   ./start.sh status     # 查看三层运行状态 + 端口
#   ./start.sh logs       # tail 各层日志（Ctrl-C 退出）
#   ./start.sh check      # 健康检查（三层 + 端到端接口探活）
#
# 端口可用环境变量覆盖：
#   BFF_PORT(8090) / CLIENT_PORT(5190)
# MCP 端点读 agx_config.json 的 mcp.url（可用 BFF_MCP_URL 覆盖）
#
# ⚠️ 回滚：WEBTOOLS_LEGACY_SSE=1 ./start.sh start
#    会额外拉起旧的 31888 SSE 实例并让 BFF 连它（BFF_MCP_URL 指向 /sse）。
# ─────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGX_ROOT="$(cd "$HERE/.." && pwd)"   # webtools 的父目录即 agx 根
PY="$AGX_ROOT/.venv/bin/python"

CLIENT_DIR="$HERE/client"
SERVER_DIR="$HERE/server"

BFF_PORT="${BFF_PORT:-8090}"
CLIENT_PORT="${CLIENT_PORT:-5190}"

BFF_PIDFILE="/tmp/win3_bff.pid"
BFF_LOGFILE="/tmp/win3_bff.log"
CLIENT_PIDFILE="/tmp/win3_client.pid"
CLIENT_LOGFILE="/tmp/win3_client.log"

MCP_SCRIPT="$SERVER_DIR/run_mcp_sse.sh"

# ── 小工具 ────────────────────────────────────────────────
c() { printf "\033[%sm%s\033[0m" "$1" "$2"; }          # 上色
ok()   { echo "$(c 32 "✅") $*"; }
warn() { echo "$(c 33 "⚠️ ")$*"; }
err()  { echo "$(c 31 "❌") $*" >&2; }
hr()   { echo "────────────────────────────────────────────────"; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# ── 凭据注入（2026-11-13 产品负责人定：env 优先、json 兜底）──────────────
# 背景：账密已从 agx_config.json 迁到 ~/.agx/agx_creds.env，
#       但 BFF 之前直接读 json → json 一移走就全站 500（Access denied，已实际发生）。
# 这里在启动子进程**之前**把凭据注入环境：子进程继承后，即使配置文件缺失也能连库。
# ★ 不覆盖已有环境变量（与 config_loader._load_creds_env 同语义）：
#   临时 `AGX_DB_PASS=xxx ./start.sh start` 仍然有效，文件不该反过来压过显式覆盖。
# ★ 文件名优先级：AGX_CREDS_ENV → ~/.agx/agx_creds.env → <root>/mysql_env.sh（旧约定兼容）。
load_creds_env() {
  local f
  for f in "${AGX_CREDS_ENV:-}" "$HOME/.agx/agx_creds.env" "$AGX_ROOT/mysql_env.sh"; do
    [ -n "$f" ] && [ -f "$f" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in ''|\#*) continue ;; esac
      case "$line" in *=*) ;; *) continue ;; esac
      local k="${line%%=*}" v="${line#*=}"
      k="${k#"${k%%[![:space:]]*}"}"; k="${k%"${k##*[![:space:]]}"}"
      k="${k#export }"
      v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
      case "$v" in \"*\"|\\'*\\') v="${v:1:${#v}-2}" ;; esac
      [ -n "$k" ] || continue
      [ -n "${!k:-}" ] && continue
      export "$k=$v"
    done < "$f"
    # 只吃第一个存在的文件（与 Python 侧一致的优先级，不叠着覆盖）
    break
  done
}

# 从主配置读 MCP 端点（单一真相源：agx_config.json 的 mcp.url）
mcp_url() {
  AGX_ROOT="$AGX_ROOT" "$PY" - <<'PYEOF'
import json, os, pathlib
root = pathlib.Path(os.environ["AGX_ROOT"])
url = "http://127.0.0.1:31890/agentgeex/mcp"
try:
    m = (json.load(open(root / "agx_config.json", encoding="utf-8")) or {}).get("mcp") or {}
    url = str(m.get("url") or url)
except Exception:
    pass
print(os.environ.get("BFF_MCP_URL", url))
PYEOF
}

# 凭据先入环境（BFF / 探活子进程都要用）—— 放在函数定义之后、首次使用之前
load_creds_env

# 从 URL 取 host:port（探活只需要这一半）
url_host_port() {
  "$PY" -c 'import sys,urllib.parse as u; p=u.urlparse(sys.argv[1]); print(p.hostname or "127.0.0.1", p.port or 80)' "$1"
}

wait_port() {  # $1=port $2=秒数 $3=标签
  local p="$1" t="$2" label="$3" i=0
  while [ "$i" -lt "$t" ]; do
    if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then return 0; fi
    sleep 1; i=$((i + 1))
  done
  return 1
}

# ── ① MCP（共享网关）────────────────────────────────────
# 职责边界：**探活，不启停**。网关归 PM2 管（agx-mcp-gateway），
# 这里再起一份会抢 31890 端口 → PM2 反复重启（文档已记录该坑）。
mcp_start() {
  local URL HOST PORT
  URL="$(mcp_url)"
  read -r HOST PORT <<< "$(url_host_port "$URL")"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ok "MCP 网关已在运行 (:${PORT})  $URL"
  else
    err "MCP 网关未监听 (:${PORT}) —— BFF 取不到数"
    echo "   启动：pm2 restart agx-mcp-gateway   （或 pm2 resurrect）"
    echo "   文档：docs/agent_docs/2026-11-04-mcp-service-layer-ops.md"
    return 1
  fi
  # 旧 SSE 实例（31888）：BFF 已不依赖，但可能有外部客户端连着 → 只提示，不停。
  if lsof -nP -iTCP:31888 -sTCP:LISTEN >/dev/null 2>&1; then
    warn "旧 SSE 实例仍在 31888 监听（BFF 已不依赖；如有外部客户端请勿随意停）"
  fi
  if [ "${WEBTOOLS_LEGACY_SSE:-0}" = "1" ]; then
    echo "→ WEBTOOLS_LEGACY_SSE=1：拉起旧的 31888 SSE 实例（回滚路径）"
    "$MCP_SCRIPT" start >/dev/null 2>&1 || warn "旧 SSE 启动失败，见 $MCP_SCRIPT logs"
  fi
}
mcp_stop() {
  if [ "${WEBTOOLS_LEGACY_SSE:-0}" = "1" ]; then
    "$MCP_SCRIPT" stop 2>&1 | sed 's/^/  MCP SSE(legacy): /' || true
  else
    echo "  MCP 网关归 PM2 管，本脚本不停它（要停：pm2 stop agx-mcp-gateway）"
  fi
}
mcp_status() {
  local URL HOST PORT
  URL="$(mcp_url)"
  read -r HOST PORT <<< "$(url_host_port "$URL")"
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "运行中 (:${PORT}) $URL"
  else
    echo "未运行 (:${PORT} 未监听)"
  fi
}

# ── ② BFF (FastAPI) ──────────────────────────────────────
bff_start() {
  if lsof -nP -iTCP:"$BFF_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ok "BFF 已在运行 (:${BFF_PORT})"
    return 0
  fi
  echo "→ 启动 BFF @ http://127.0.0.1:$BFF_PORT ..."
  cd "$SERVER_DIR"
  BFF_PORT="$BFF_PORT" nohup ./run.sh >"$BFF_LOGFILE" 2>&1 &
  echo $! > "$BFF_PIDFILE"
  cd "$HERE"
  if wait_port "$BFF_PORT" 30 "BFF"; then
    ok "BFF 就绪 (:${BFF_PORT})  日志: $BFF_LOGFILE"
  else
    err "BFF 启动超时，见 $BFF_LOGFILE"; tail -20 "$BFF_LOGFILE" >&2 || true; return 1
  fi
}
bff_stop() {
  # 优先按 pidfile；兜底按端口（uvicorn --reload 会派生子进程，需杀整棵树）
  if pid_alive "$BFF_PIDFILE"; then
    pkill -P "$(cat "$BFF_PIDFILE")" 2>/dev/null || true
    kill "$(cat "$BFF_PIDFILE")" 2>/dev/null || true
  fi
  local pids; pids="$(lsof -nP -tiTCP:"$BFF_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
  pkill -f "uvicorn app.main" 2>/dev/null || true
  rm -f "$BFF_PIDFILE"
  _wait_port_free "$BFF_PORT"
  echo "已停止 BFF"
}
_wait_port_free() {  # 等待端口释放（最多 5s，避免 status 误报）
  local p="$1" i=0
  while [ "$i" -lt 5 ] && lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; do
    sleep 1; i=$((i + 1))
  done
}
bff_status() {
  if lsof -nP -iTCP:"$BFF_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "运行中 (:${BFF_PORT})"
  else
    echo "未运行"
  fi
}

# ── ③ 前端 (Vite) ────────────────────────────────────────
client_start() {
  if lsof -nP -iTCP:"$CLIENT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    ok "前端已在运行 (:${CLIENT_PORT})"
    return 0
  fi
  echo "→ 启动前端 @ http://localhost:$CLIENT_PORT ..."
  cd "$CLIENT_DIR"
  nohup npm run dev >"$CLIENT_LOGFILE" 2>&1 &
  echo $! > "$CLIENT_PIDFILE"
  cd "$HERE"
  if wait_port "$CLIENT_PORT" 90 "前端"; then
    ok "前端就绪 (:${CLIENT_PORT})  日志: $CLIENT_LOGFILE"
  else
    err "前端启动超时，见 $CLIENT_LOGFILE"; tail -20 "$CLIENT_LOGFILE" >&2 || true; return 1
  fi
}
client_stop() {
  if pid_alive "$CLIENT_PIDFILE"; then
    pkill -P "$(cat "$CLIENT_PIDFILE")" 2>/dev/null || true
    kill "$(cat "$CLIENT_PIDFILE")" 2>/dev/null || true
  fi
  local pids; pids="$(lsof -nP -tiTCP:"$CLIENT_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
  pkill -f "vite --host" 2>/dev/null || true
  rm -f "$CLIENT_PIDFILE"
  _wait_port_free "$CLIENT_PORT"
  echo "已停止前端"
}
client_status() {
  if lsof -nP -iTCP:"$CLIENT_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "运行中 (:${CLIENT_PORT})"
  else
    echo "未运行"
  fi
}

# ── 健康检查 ──────────────────────────────────────────────
health_check() {
  hr
  echo "🏥 Win3 健康检查"
  hr
  # ① MCP 网关（另附 BFF 实际连的地址，防止"端口通了但连的是别的实例"）
  local MURL MPORT
  MURL="$(mcp_url)"
  read -r _ MPORT <<< "$(url_host_port "$MURL")"
  if lsof -nP -iTCP:"$MPORT" -sTCP:LISTEN >/dev/null 2>&1; then ok "MCP 网关 :${MPORT}"; else err "MCP 网关 :${MPORT} 未监听"; fi
  # ② BFF readyz
  local rz; rz="$(curl -s -m 5 "http://127.0.0.1:${BFF_PORT}/readyz" || true)"
  if echo "$rz" | grep -q '"status":"ok"'; then
    ok "BFF :${BFF_PORT} readyz ok ($(echo "$rz" | grep -o '"mysql":[a-z]*' ), $(echo "$rz" | grep -o '"mcp_sse":[a-z]*'))"
  else
    err "BFF :${BFF_PORT} readyz 失败"
  fi
  # ③ 前端
  if curl -s -m 5 -o /dev/null -w "%{http_code}" "http://localhost:${CLIENT_PORT}/" | grep -q 200; then
    ok "前端 :${CLIENT_PORT} HTTP 200"
  else
    err "前端 :${CLIENT_PORT} 不可达"
  fi
  # ④ 端到端：登录 → 拉 agents 计数
  local tok
  tok="$(curl -s -m 8 -X POST "http://127.0.0.1:${BFF_PORT}/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"username":"agx_ui_lead","password":"startup-check"}' \
        | "$PY" -c "import sys,json;print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)"
  if [ -n "$tok" ]; then
    local total
    total="$(curl -s -m 15 -D - "http://127.0.0.1:${BFF_PORT}/v1/agents?_page=1&_limit=500" \
             -H "Authorization: Bearer $tok" -o /dev/null \
             | grep -i '^x-total-count' | tr -d '\r' | awk '{print $2}')"
    ok "端到端 /v1/agents 总数 = ${total:-?}（登录+鉴权+取数全通）"
  else
    warn "端到端登录未拿到 token（BFF 可能仍在启动）"
  fi
  hr
}

# ── 命令分发 ──────────────────────────────────────────────
case "${1:-start}" in
  start)
    echo "🚀 启动 Win3 商业控制台（三层）"
    hr
    mcp_start
    bff_start
    client_start
    hr
    ok "全部就绪 → 打开 $(c 36 "http://localhost:${CLIENT_PORT}/")"
    ;;
  stop)
    echo "🛑 停止 Win3 商业控制台"
    client_stop
    bff_stop
    mcp_stop
    ok "已停止全部"
    ;;
  restart)
    "$0" stop
    sleep 2
    "$0" start
    ;;
  status)
    hr
    echo "📊 Win3 运行状态"
    hr
    printf "① MCP SSE  : %s\n" "$(mcp_status | head -1)"
    printf "② BFF       : %s\n" "$(bff_status)"
    printf "③ 前端      : %s\n" "$(client_status)"
    hr
    ;;
  logs)
    echo "tail 日志（Ctrl-C 退出）: $BFF_LOGFILE | $CLIENT_LOGFILE"
    tail -f "$BFF_LOGFILE" "$CLIENT_LOGFILE"
    ;;
  check)
    health_check
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs|check}" >&2
    exit 1
    ;;
esac
