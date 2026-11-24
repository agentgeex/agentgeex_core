#!/usr/bin/env bash
# =============================================================================
# start_qwen_asr.sh -- one-shot launcher for the Qwen3-ASR-1.7B speech service
#
# Usage:
#   ./start_qwen_asr.sh start     start in the background (writes a PID file, logs under logs/)
#   ./start_qwen_asr.sh stop      stop
#   ./start_qwen_asr.sh restart   restart
#   ./start_qwen_asr.sh status    show status
#   ./start_qwen_asr.sh log       follow the log (Ctrl+C exits; the service keeps running)
#   ./start_qwen_asr.sh check     environment self-check only; does not start
#   ./start_qwen_asr.sh run       run in the foreground (use this for pm2 / systemd)
#
# pm2 supervision:
#   pm2 start ./start_qwen_asr.sh --name qwen-asr -- run
#   pm2 save
#
# * Why a separate script: audio_service_server_qwen_asr.py depends on 4 files in the same directory,
#   one venv and one model directory. Missing any of them gives "No module named" or "model directory not found".
#   This script checks every prerequisite first and tells you how to fix what is missing, instead of leaving you to read a stack trace.
#
# Overridable environment variables (auto-detected when unset):
#   QWEN_ASR_PYTHON   interpreter path (must be able to import qwen_asr and websockets)
#   QWEN_ASR_MODEL    weights directory (must contain config.json and *.safetensors)
#   QWEN_ASR_APP_DIR  code directory (default = the directory this script lives in)
#   ASR_HOST          bind address, default 0.0.0.0
#   ASR_PORT          bind port, default 18888
#   QWEN_ASR_LANG     force the language: Chinese/English; default empty = auto-detect
#   QWEN_ASR_ITN_POST 1=enable Chinese-numeral mapping, default 0=off
# =============================================================================
set -uo pipefail

# ---------- Paths ----------
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${QWEN_ASR_APP_DIR:-$SELF}"
ENTRY="$APP_DIR/audio_service_server_qwen_asr.py"
RUN_DIR="${QWEN_ASR_RUN_DIR:-$APP_DIR/run}"
LOG_DIR="${QWEN_ASR_LOG_DIR:-$APP_DIR/logs}"
PID_FILE="$RUN_DIR/qwen_asr.pid"
LOG_FILE="$LOG_DIR/qwen_asr.log"
ERR_FILE="$LOG_DIR/qwen_asr.err.log"

HOST="${ASR_HOST:-0.0.0.0}"
PORT="${ASR_PORT:-18888}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# ---------- Colors ----------
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[36m'; N=$'\033[0m'
else
  R=; G=; Y=; B=; N=
fi
info() { printf '%s[asr]%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '%s[ok ]%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# ---------- Interpreter detection ----------
find_python() {
  local cand
  if [ -n "${QWEN_ASR_PYTHON:-}" ]; then
    [ -x "$QWEN_ASR_PYTHON" ] || die "QWEN_ASR_PYTHON 指向的文件不可执行: $QWEN_ASR_PYTHON"
    echo "$QWEN_ASR_PYTHON"; return
  fi
  for cand in \
      "$APP_DIR/../venv-qwen-asr/bin/python" \
      "$APP_DIR/venv-qwen-asr/bin/python" \
      "$HOME/venv-qwen-asr/bin/python" \
      "$APP_DIR/.venv/bin/python" \
      "$APP_DIR/../.venv/bin/python"; do
    if [ -x "$cand" ] && "$cand" -c 'import qwen_asr' >/dev/null 2>&1; then
      echo "$(cd "$(dirname "$cand")" && pwd)/$(basename "$cand")"; return
    fi
  done
  # Last resort: python3 on PATH, but it must be able to import qwen_asr
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import qwen_asr' >/dev/null 2>&1; then
    command -v python3; return
  fi
  echo ""
}

# ---------- Model directory detection ----------
find_model() {
  local cand
  if [ -n "${QWEN_ASR_MODEL:-}" ]; then
    echo "${QWEN_ASR_MODEL/#\~/$HOME}"; return
  fi
  for cand in \
      "$HOME/.cache/modelscope/hub/models/Qwen/Qwen3-ASR-1.7B" \
      "$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B/snapshots" \
      "/home/mat/robertfeng/models/Qwen3-ASR-1.7B"; do
    if [ -f "$cand/config.json" ]; then echo "$cand"; return; fi
  done
  echo ""
}

# ---------- Environment self-check ----------
PY=""
MODEL=""

check_env() {
  local fail=0

  [ -f "$ENTRY" ] || { printf '%s[FAIL]%s 入口文件不存在: %s\n' "$R" "$N" "$ENTRY" >&2; fail=1; }

  # Dependencies in the same directory
  local f
  for f in cn_num_normalize.py _models.py _logging.py audio_decode.py; do
    [ -f "$APP_DIR/$f" ] || {
      printf '%s[FAIL]%s 缺同目录文件: %s/%s\n' "$R" "$N" "$APP_DIR" "$f" >&2; fail=1; }
  done
  [ "$fail" -eq 1 ] && die "代码不全，把仓库 tools/audio_stt/ 整个目录一起拷过来。"

  PY="$(find_python)"
  if [ -z "$PY" ]; then
    cat >&2 <<'EOF'
[FAIL] 找不到能 import qwen_asr 的 python 解释器。

请先建 venv 并装依赖（三个坑都在这一步）：
  uv venv <BASE>/venv-qwen-asr --python 3.12
  uv pip install --python <BASE>/venv-qwen-asr/bin/python \
      --index-url https://mirrors.aliyun.com/pypi/simple/ \
      --reinstall-package torch "torch==2.7.0" "qwen-asr" "websockets" "uvloop"

  ★ torch 必须钉 2.7.0（cu126），否则 torch.cuda.is_available() 是 False
  ★ pypi.org 不通时用上面这个阿里镜像
  ★ 漏装 websockets 会导致 uvicorn 静默降级、所有 WebSocket 请求 404

装完用环境变量指定：
  QWEN_ASR_PYTHON=<BASE>/venv-qwen-asr/bin/python ./start_qwen_asr.sh check
EOF
    exit 1
  fi
  ok "解释器: $PY"

  # Key dependencies
  "$PY" -c 'import torch' >/dev/null 2>&1 || die "该解释器没有 torch"
  local cuda
  cuda="$("$PY" -c 'import torch;print(torch.cuda.is_available())' 2>/dev/null)"
  if [ "$cuda" = "True" ]; then
    local gpu
    gpu="$("$PY" -c 'import torch;print(torch.cuda.get_device_name(0))' 2>/dev/null)"
    ok "CUDA 可用: $gpu"
  else
    warn "torch.cuda.is_available() = False —— 会退回 CPU，慢几十倍。"
    warn "多半是 torch 版本与驱动不匹配：钉 torch==2.7.0（cu126）重装。"
  fi
  "$PY" -c 'import websockets' >/dev/null 2>&1 \
    || die "缺 websockets → uvicorn 会静默降级成纯 HTTP，所有 WS 请求 404。装它：$PY -m pip install websockets"
  ok "websockets 已装"
  "$PY" -c 'import uvloop' >/dev/null 2>&1 || warn "没装 uvloop（不致命，性能略低）"

  MODEL="$(find_model)"
  if [ -z "$MODEL" ]; then
    cat >&2 <<'EOF'
[FAIL] 找不到模型目录（要有 config.json + *.safetensors，共约 4.7 GB）。

下载（4.7 GB，建议放数据盘而不是系统盘）：
  modelscope download --model Qwen/Qwen3-ASR-1.7B \
      --local_dir <BASE>/models/Qwen3-ASR-1.7B

然后指定：QWEN_ASR_MODEL=<BASE>/models/Qwen3-ASR-1.7B
EOF
    exit 1
  fi
  local nfile
  nfile="$(find "$MODEL" -maxdepth 1 -name '*.safetensors' 2>/dev/null | wc -l | tr -d ' ')"
  [ "$nfile" -gt 0 ] || die "模型目录里没有 *.safetensors（下载没完成？）: $MODEL"
  ok "模型: $MODEL ($nfile 个 safetensors)"
}

# ---------- Port occupancy ----------
port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null
  elif command -v ss >/dev/null 2>&1; then
    ss -lntp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $NF}' \
      | sed -E 's/.*pid=([0-9]+).*/\1/' | grep -E '^[0-9]+$'
  fi
}

read_pid() {
  [ -f "$PID_FILE" ] || return 1
  local p
  p="$(head -1 "$PID_FILE" 2>/dev/null)"
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  echo "$p"
}

# ---------- Actions ----------
do_check() {
  info "代码目录: $APP_DIR"
  info "监听计划: $HOST:$PORT"
  check_env
  ok "自检通过，可以 start"
}

do_run() {
  check_env
  info "前台运行 入口=$(basename "$ENTRY") 监听=$HOST:$PORT"
  info "模型=$MODEL"
  info "数字映射 QWEN_ASR_ITN_POST=${QWEN_ASR_ITN_POST:-0}（0=关，保持口语原样）"
  export QWEN_ASR_MODEL="$MODEL"
  exec "$PY" "$ENTRY" --host "$HOST" --port "$PORT"
}

do_start() {
  local p
  if p="$(read_pid)"; then
    warn "已在运行 (pid=$p)，要先停用: $0 restart"
    return 0
  fi
  check_env

  local others
  others="$(port_pids | tr '\n' ' ')"
  if [ -n "${others// /}" ]; then
    die "端口 $PORT 已被占用（pid: $others）。改端口：ASR_PORT=别的 $0 start"
  fi

  info "启动中... 入口=$(basename "$ENTRY") 监听=$HOST:$PORT"
  QWEN_ASR_MODEL="$MODEL" nohup "$PY" "$ENTRY" \
      --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>> "$ERR_FILE" &
  local child=$!
  { echo "$child"; echo "$ENTRY"; } > "$PID_FILE"

  # Wait for the port: model loading takes 5-20 seconds, so wait up to 90 seconds
  local i
  for i in $(seq 1 90); do
    if ! kill -0 "$child" 2>/dev/null; then
      printf '%s[FAIL]%s 进程启动后立刻退出。错误日志最后 20 行：\n' "$R" "$N" >&2
      tail -20 "$ERR_FILE" >&2
      rm -f "$PID_FILE"
      return 1
    fi
    if port_pids | grep -qx "$child"; then
      ok "已启动 pid=$child  端口 $PORT"
      info "日志: $LOG_FILE"
      info "错误: $ERR_FILE"
      return 0
    fi
    sleep 1
  done
  warn "90 秒内没等到端口就绪（模型加载慢？）。进程仍在: pid=$child"
  info "看日志: tail -f $ERR_FILE"
  return 0
}

do_stop() {
  local p
  if ! p="$(read_pid)"; then
    # Even with the PID file lost, try to clean up: kill this directory's process listening on this port
    local q
    for q in $(port_pids); do
      if ps -p "$q" -o command= 2>/dev/null | grep -qF "$APP_DIR"; then
        warn "发现孤儿进程 pid=$q，清理"
        kill "$q" 2>/dev/null
      fi
    done
    rm -f "$PID_FILE"
    info "未在运行"
    return 0
  fi
  info "停止 pid=$p"
  kill "$p" 2>/dev/null
  local i
  for i in $(seq 1 20); do
    kill -0 "$p" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$p" 2>/dev/null; then
    warn "没响应 SIGTERM，强杀"
    kill -9 "$p" 2>/dev/null
  fi
  rm -f "$PID_FILE"
  ok "已停止"
}

do_status() {
  local p
  if p="$(read_pid)"; then
    ok "运行中 pid=$p"
    local rss
    rss="$(ps -p "$p" -o rss= 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')"
    info "内存 $rss  端口 $PORT"
    if port_pids | grep -qx "$p"; then
      ok "端口 $PORT 已监听"
    else
      warn "进程在但端口未监听（还在加载模型？）"
    fi
  else
    info "未运行"
    local others
    others="$(port_pids | tr '\n' ' ')"
    [ -n "${others// /}" ] && warn "但端口 $PORT 被别的进程占着: $others"
    return 1
  fi
}

do_log() {
  touch "$LOG_FILE" "$ERR_FILE"
  info "跟日志（Ctrl+C 退出，不影响服务）：$ERR_FILE + $LOG_FILE"
  tail -n 40 -f "$ERR_FILE" "$LOG_FILE"
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; sleep 1; do_start ;;
  status)  do_status ;;
  log)     do_log ;;
  check)   do_check ;;
  run)     do_run ;;
  *)
    cat <<EOF
用法: $0 {start|stop|restart|status|log|check|run}

  start    后台启动（写 PID，日志落 $LOG_DIR/）
  stop     停止
  restart  重启
  status   看状态
  log      跟日志
  check    只做环境自检，不启动
  run      前台运行（pm2/systemd 用这个）

可覆盖的环境变量:
  QWEN_ASR_PYTHON   解释器路径（须能 import qwen_asr / websockets / torch）
  QWEN_ASR_MODEL    权重目录（含 config.json 与 *.safetensors）
  QWEN_ASR_APP_DIR  代码目录（默认= 本脚本所在目录）
  ASR_HOST / ASR_PORT      默认 0.0.0.0:18888
  QWEN_ASR_LANG     Chinese / English，默认空=自动判定
  QWEN_ASR_ITN_POST 1=开中文数字映射，默认 0=关（保持口语原样）

示例:
  ASR_PORT=9999 ./start_qwen_asr.sh start
  pm2 start ./start_qwen_asr.sh --name qwen-asr -- run
EOF
    exit 1 ;;
esac
