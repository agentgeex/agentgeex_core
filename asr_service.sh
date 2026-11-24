#!/usr/bin/env bash
# ============================================================
# asr_service.sh — ASR(SenseVoice)语音识别服务 启停脚本
#
#   用法:  ./asr_service.sh start     启动
#          ./asr_service.sh stop      停止
#          ./asr_service.sh restart   重启
#          ./asr_service.sh status    查看状态
#          ./asr_service.sh log       跟踪日志(Ctrl-C 退出)
#
# 来源: motozolo_gpt_tools/asr_service.sh，移植时只改"从哪取值"，
# 不改"怎么启停"。所有路径由脚本自身位置推导，不在任何机器上写死。
# ============================================================
set -uo pipefail
# 刻意不加 -e: lsof 无结果返回非 0 是正常分支，加 -e 会中途退出

# 本脚本安装在仓库根目录，代码在 tools/audio_stt/ 下。
# 全部路径由脚本自身位置推导 —— 在哪个 cwd 执行都一样，换机/换 clone 也不用改。
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_DIR="${ASR_TOOL_DIR:-$BASE_DIR/tools/audio_stt}"

# ---- 配置: 环境变量 > 本机 asr.env > 仓库根 asr.env > 内置默认 ----
# 三处都用 := 语义（只补空缺），所以【先 source 的优先】，顺序即优先级：
#   ① 调用方已 export 的（本进程环境，最高）
#   ② tools/audio_stt/asr.env   本机专属，已 gitignore，不进库
#   ③ <仓库根>/asr.env          共享默认，进库
# 显式指定 ASR_ENV_FILE 时只读那一个（不叠加），便于一次性试验。
#
# ★ 为什么要分层：仓库根那份跟着 git 走，改它等于改所有人的默认值。
#   而「本机要不要对外监听」是环境属性，不是项目属性 ——
#   188 是内网机器可以绑 0.0.0.0，笔记本不能。分层才两头都对。
if [ -n "${ASR_ENV_FILE:-}" ]; then
  _env_files="$ASR_ENV_FILE"
else
  _env_files="$TOOL_DIR/asr.env $BASE_DIR/asr.env"
fi
ENV_USED=""
for _f in $_env_files; do
  if [ -f "$_f" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$_f"
    set +a
    ENV_USED="${ENV_USED:+$ENV_USED, }$_f"
  fi
done
unset _f _env_files

ENTRY="${ASR_ENTRY:-$TOOL_DIR/audio_service_server.py}"
PORT="${ASR_PORT:-8888}"
HOST="${ASR_HOST:-127.0.0.1}"
ENTRY_NAME="$(basename "$ENTRY")"

RUN_DIR="${ASR_RUN_DIR:-$BASE_DIR/logs}"
LOG_FILE="$RUN_DIR/asr_server.log"
PID_FILE="$RUN_DIR/asr_server.pid"
START_TIMEOUT="${ASR_START_TIMEOUT:-60}"
STOP_TIMEOUT="${ASR_STOP_TIMEOUT:-15}"

# ------------------------------------------------------------
# Python 解释器: 必须能 import funasr，否则跑起来也是白跑
#
# 原脚本写死 /opt/miniconda3/envs/py310_motozolo/bin/python —— 换机即失效。
# 这里按优先级找: 显式指定 > 仓库 .venv > PATH 上的 python3。
# 找不到就【明确报错并给出修复命令】，而不是让服务起一半死在 ImportError。
# ------------------------------------------------------------
resolve_python() {
  if [ -n "${ASR_PYTHON:-}" ]; then
    if "$ASR_PYTHON" -c "import funasr" >/dev/null 2>&1; then
      printf '%s' "$ASR_PYTHON"; return 0
    fi
    echo "❌ ASR_PYTHON=$ASR_PYTHON 但无法 import funasr" >&2
    return 1
  fi

  local cand
  for cand in "$BASE_DIR/.venv/bin/python" "$TOOL_DIR/../../.venv/bin/python" "$(command -v python3 2>/dev/null)"; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c "import funasr" >/dev/null 2>&1; then
      printf '%s' "$cand"; return 0
    fi
  done
  return 1
}

# ---- 端口占用者 ----
pids_on_port() {
  lsof -nP -ti :"$PORT" 2>/dev/null
}

# 该 PID 是否就是本服务(而不是恰好占了同一端口的别人)
# ★ 原脚本 stop 直接 kill 占端口的任何进程。8888 是常见开发端口，
#   一旦被别的服务占用，stop 就会误杀无关进程。故先验命令行。
#
# ★ 2026-11-07 补：不能只比对本脚本"此刻解析出的入口名"。
#   实测：start 时传 ASR_ENTRY=某个非默认入口，之后 status/stop 没再传，
#   ENTRY 回落成默认的 audio_service_server.py → 自己的进程被当成"别人的"，
#   于是拒绝停止。故加两道兜底（PID 文件里记录的真实入口 + 本目录路径）。
is_ours() {
  local pid="$1" cmd rec
  [ -n "$pid" ] || return 1
  cmd="$(ps -p "$pid" -o command= 2>/dev/null)"
  [ -n "$cmd" ] || return 1

  case "$cmd" in *"$ENTRY_NAME"*) return 0 ;; esac

  if [ -f "$PID_FILE" ]; then
    rec="$(sed -n '2p' "$PID_FILE" 2>/dev/null)"
    if [ -n "$rec" ]; then
      case "$cmd" in *"$(basename "$rec")"*) return 0 ;; esac
    fi
  fi

  case "$cmd" in *"/tools/audio_stt/"*) return 0 ;; esac
  return 1
}

collect() {          # $1=ours|foreign  → 输出 PID 列表
  local want="$1" pid
  for pid in $(pids_on_port); do
    if is_ours "$pid"; then
      [ "$want" = ours ] && echo "$pid"
    else
      [ "$want" = foreign ] && echo "$pid"
    fi
  done
}

# ---- 启动 ----
do_start() {
  local foreign
  foreign="$(collect foreign)"
  if [ -n "$foreign" ]; then
    echo "❌ 端口 $PORT 被【其它进程】占用，不是本服务，拒绝启动："
    for pid in $foreign; do
      echo "     PID $pid  $(ps -p "$pid" -o command= 2>/dev/null | cut -c1-100)"
    done
    echo "    先用 ASR_PORT=其他端口 启动，或自行处理该进程。"
    return 1
  fi

  local ours
  ours="$(collect ours)"
  if [ -n "$ours" ]; then
    echo "⚠️   ASR 已在运行 (PID: $(echo "$ours" | tr '\n' ' '))，端口 $PORT 被占用。"
    echo "    如需重启请用: $0 restart"
    return 0
  fi

  if [ ! -f "$ENTRY" ]; then
    echo "❌ 入口文件不存在: $ENTRY"
    return 1
  fi

  local py
  if ! py="$(resolve_python)"; then
    echo "❌ 找不到装有 funasr 的 Python 解释器。"
    echo "   在本仓库根目录执行:  uv sync --extra audio"
    echo "   或显式指定:        ASR_PYTHON=/path/to/python $0 start"
    return 1
  fi

  mkdir -p "$RUN_DIR"

  echo "🚀 启动 ASR 服务 ..."
  echo "   入口: $ENTRY_NAME"
  echo "   代码: $TOOL_DIR"
  echo "   Python: $py"
  echo "   监听: $HOST:$PORT"
  echo "   配置: ${ENV_USED:-<无，全走内置默认>}"
  echo "   模型: ${VOICE_MODEL:-<内置默认>}"
  echo "   日志: $LOG_FILE"

  nohup "$py" "$ENTRY" --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
  local child=$!
  # 第一行 PID（人看），第二行真实入口路径（status/stop 认自己用）
  { echo "$child"; echo "$ENTRY"; } > "$PID_FILE"

  echo -n "   等待 $HOST:$PORT 就绪"
  local i
  for i in $(seq 1 "$START_TIMEOUT"); do
    ours="$(collect ours)"
    if [ -n "$ours" ]; then
      echo ""
      echo "✅ ASR 已启动 (PID: $(echo "$ours" | tr '\n' ' '))"
      echo "   验证: lsof -nP -i :$PORT"
      echo "   日志: tail -f $LOG_FILE"
      return 0
    fi
    if ! kill -0 "$child" 2>/dev/null; then
      echo ""
      echo "❌ 进程启动后退出，日志尾部:"
      tail -n 20 "$LOG_FILE" 2>/dev/null | sed 's/^/     /'
      return 1
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  echo "⚠️  ${START_TIMEOUT}s 内未监听 $PORT（进程仍在，可能有别的异常）"
  echo "   日志: $LOG_FILE"
  return 1
}

# ---- 停止 ----
# 该 PID 是否由进程管理器（PM2）托管。
# ★ 2026-11-08：进程被 PM2 接管后，本脚本的 stop 是错的工具 ——
#   kill 掉 python，PM2 立刻按 autorestart 再拉起来，人看到的现象是
#   「stop 了但它又活了」，然后反复 stop 反复起，日志刷满。
#   所以这里只认特征（父进程是 PM2 / node 守护），不做静默 kill。
is_pm2_managed() {
  local pid="$1" ppid ppname
  [ -n "$pid" ] || return 1
  ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  [ -n "$ppid" ] || return 1
  ppname="$(ps -o comm= -p "$ppid" 2>/dev/null)"
  case "$ppname" in
    *PM2*|*pm2*) return 0 ;;
  esac
  # PM2 God daemon 有时进程名就是 node，再看一眼它的命令行
  case "$(ps -o command= -p "$ppid" 2>/dev/null)" in
    *PM2*|*God*) return 0 ;;
  esac
  return 1
}

do_stop() {
  local ours foreign
  ours="$(collect ours)"
  foreign="$(collect foreign)"

  if [ -z "$ours" ] && [ -z "$foreign" ]; then
    echo "ℹ️  ASR 未在运行(端口 $PORT 无监听)。"
    rm -f "$PID_FILE"
    return 0
  fi

  if [ -n "$foreign" ]; then
    echo "⚠️  端口 $PORT 上有【不属于本服务】的进程，不动它："
    for pid in $foreign; do
      echo "     PID $pid  $(ps -p "$pid" -o command= 2>/dev/null | cut -c1-100)"
    done
    [ -z "$ours" ] && { rm -f "$PID_FILE"; return 1; }
  fi

  local pm2_pids=""
  for pid in $ours; do
    if is_pm2_managed "$pid"; then pm2_pids="$pm2_pids $pid"; fi
  done
  if [ -n "$pm2_pids" ]; then
    echo "⚠️  PID$pm2_pids 由 PM2 托管，本脚本不插手（kill 会被自动拉起）。"
    echo "    请用:  pm2 stop agx-asr      停"
    echo "           pm2 restart agx-asr   重启（改完 asr.env 后）"
    echo "           pm2 logs agx-asr      看日志"
    return 1
  fi

  echo "🛑 停止 ASR (PID: $(echo "$ours" | tr '\n' ' ')) ..."
  # shellcheck disable=SC2086
  kill $ours 2>/dev/null

  local i
  for i in $(seq 1 "$STOP_TIMEOUT"); do
    if [ -z "$(collect ours)" ]; then
      echo "✅ 已停止。"
      rm -f "$PID_FILE"
      return 0
    fi
    sleep 1
  done

  echo "⚠️  优雅停止超时，强制 kill -9 ..."
  # shellcheck disable=SC2046
  kill -9 $(collect ours) 2>/dev/null
  sleep 1
  if [ -n "$(collect ours)" ]; then
    echo "❌ 仍无法停止，请手动处理: lsof -nP -i :$PORT"
    return 1
  fi
  echo "✅ 已强制停止。"
  rm -f "$PID_FILE"
}

# ---- 状态 ----
do_status() {
  local ours foreign
  ours="$(collect ours)"
  foreign="$(collect foreign)"

  if [ -n "$ours" ]; then
    echo "✅ ASR 运行中 (PID: $(echo "$ours" | tr '\n' ' '))，监听 $HOST:$PORT"
    [ -f "$PID_FILE" ] && echo "   启动记录 PID: $(sed -n '1p' "$PID_FILE")"
    echo "   日志: $LOG_FILE"
  else
    echo "⛔ ASR 未运行(端口 $PORT 无本服务监听)。"
  fi

  if [ -n "$foreign" ]; then
    echo "⚠️  同一端口上还有非本服务进程："
    for pid in $foreign; do
      echo "     PID $pid  $(ps -p "$pid" -o command= 2>/dev/null | cut -c1-100)"
    done
  fi
}

# ---- 日志 ----
do_log() {
  if [ -f "$LOG_FILE" ]; then
    tail -f "$LOG_FILE"
  else
    echo "❌ 日志文件不存在: $LOG_FILE"
    exit 1
  fi
}

# ---- 前台运行 ----
# 给进程管理器（PM2 / systemd / docker）当 entrypoint 用。
do_run() {
  if [ ! -f "$ENTRY" ]; then
    echo "❌ 入口文件不存在: $ENTRY" >&2
    return 1
  fi

  local py
  if ! py="$(resolve_python)"; then
    echo "❌ 找不到装有 funasr 的 Python 解释器。" >&2
    echo "   在本仓库根目录执行:  uv sync --extra audio" >&2
    echo "   或显式指定:        ASR_PYTHON=/path/to/python $0 run" >&2
    return 1
  fi

  echo "[asr] 入口=$ENTRY_NAME 监听=$HOST:$PORT 配置=${ENV_USED:-<内置默认>}" >&2
  echo "[asr] python=$py 模型=${VOICE_MODEL:-<内置默认>}" >&2

  # exec：让 python 接管 PID 1 位 —— 进程管理器的信号（SIGTERM）必须直达它，
  # 否则停服务时 bash 先死、python 变孤儿继续占着端口和显存。
  exec "$py" "$ENTRY" --host "$HOST" --port "$PORT"
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; sleep 1; do_start ;;
  run)     do_run ;;
  status)  do_status ;;
  log)     do_log ;;
  *)
    echo "用法: $0 {start|stop|restart|status|log|run}"
    echo "  start/stop/restart  自己带 PID 文件与后台化（人手动用时）"
    echo "  run                 前台运行，不管 PID（PM2/systemd 用这个）"
    echo "可选环境变量: ASR_PYTHON ASR_PORT ASR_HOST ASR_ENTRY ASR_TOOL_DIR ASR_ENV_FILE ASR_RUN_DIR"
    exit 1
    ;;
esac
