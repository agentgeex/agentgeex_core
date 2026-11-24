# tools/audio_stt — 本地语音转文字（SenseVoice / funasr）

离线中文语音识别，模型 `iic/SenseVoiceSmall` + `speech_fsmn_vad_zh-cn-16k-common-pytorch`。
不联网、不上传音频、CPU/MPS/CUDA 都能跑。

> **来源**：`motozolo_gpt_tools/reasoning_service/audio`（2026-11-06 迁入）
> 算法与推理参数**原样保留**（`language=zh` / `use_itn=True` / `batch_size_s=60` /
> `merge_vad=True` / `merge_length_s=15` / `rich_transcription_postprocess`）。
> 只做了依赖解耦与两处显性修正，逐条列在文末「迁移改动」。

## 唯一入口

| 入口 | 形态 | 默认端口 | 适用 |
|---|---|---|---|
| `audio_service_server.py` | FastAPI **WebSocket** | `127.0.0.1:8888` | **生产在用**：webtools BFF → WS 送音频块 → 回文本 + `AUDIO_PROCESSING_COMPLETE` |

协议就两条：连上后把音频整包发一次，再发一个空包（表示结束），然后收两条文本
（第一条结果、第二条 `AUDIO_PROCESSING_COMPLETE`）。

> 曾另有两个形态（`sensevoice_server.py` HTTP :8899、`sensevoice_cli.py` CLI），
> 2026-11-07 删除。理由：它们只是同一模型的另一种壳，不解决任何新问题，
> 却让「到底该用哪个」变成每次都要问一遍的事 —— 删掉比维护好。
> 需要单次转写可直接用本文件的 Python 用法（见下），不必再起一个服务。

### Python 内嵌调用（不起服务）

```python
from tools.audio_stt.audio_service_server import ASREngine
engine = ASREngine()
print(engine.transcribe_bytes(open('/tmp/a.wav', 'rb').read()))
```

## 依赖

重依赖（torch / funasr）放在 **optional extra**，不装也能 import 本目录的模块：

```bash
uv sync                 # 核心：MCP / daemon / watchdog，不带 STT
uv sync --extra audio   # 需要语音转文字时才装
```

模型不在 pip 包里，首次运行前先下载到 `~/.cache/modelscope`：

```bash
python -c "from modelscope import snapshot_download as s; \
  s('iic/SenseVoiceSmall'); \
  s('iic/speech_fsmn_vad_zh-cn-16k-common-pytorch')"
```

路径由**仓库根的 `asr.env`** 显式指定（进库，用 `$HOME` 展开，不写死用户名）：

```bash
VOICE_MODEL=$HOME/.cache/modelscope/hub/models/iic/SenseVoiceSmall
VAD_MODEL=$HOME/.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
```

`asr_service.sh` 启动时会 source 它，并把实际路径打出来（`模型: /Users/…/SenseVoiceSmall`）。
直接跑 python 入口而不走脚本时，环境变量未设，代码会回落到同样的默认路径 ——
两条路的路径一致，但**只有脚本会把“用的是哪个模型”说给人听**。

**目录不存在怎么办**：自动回落到 model id（`iic/SenseVoiceSmall`），由 funasr 自己
下载到默认缓存，日志里打出「原配置值 + 为什么 + 下载多大」：

```
[WARNING] [模型] VOICE_MODEL=/tmp/x（配置的路径不存在）→ 回落到 model id
'iic/SenseVoiceSmall'，首次将由 modelscope 下载（约 900MB，慢是正常的）
```

这个兜底解决的是「新机器 clone 下来直接跑」：不装模型也能起来，只是第一次慢。
原来的行为是拿一个不存在的目录去 `AutoModel`，报一句看不出原因的错误。

## 跑起来

```bash
# 起服务（推荐走脚本，自带 PID/日志/就绪等待）
./asr_service.sh start          # 默认 audio_service_server.py :8888
```

`--host` 默认 `127.0.0.1`。要对外暴露必须显式 `--host 0.0.0.0`——
这些接口没有鉴权，绑到 `0.0.0.0` 等于同网段任何人都能用本机算力。

确实需要对外（如内网服务器、容器互通），用**本机覆盖文件**开，别改仓库里那份：

```bash
echo ': "${ASR_HOST:=0.0.0.0}"' > tools/audio_stt/asr.env   # 本机专属，不进库
./asr_service.sh restart
```

这样「这台机器对外」是环境事实，不会跟着 git 跑到别人的笔记本上。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `VOICE_MODEL` / `VAD_MODEL` | 仓库根 `asr.env`（进库） | 模型目录 |
| `ASR_HOST` / `ASR_PORT` | `127.0.0.1` / `8888` | WS 服务绑定 |
| `FFMPEG_BIN` | `ffmpeg` | 解任意容器音频 |
| `ASR_LOG_LEVEL` | `INFO` | 日志级别 |
| `ASR_DEBUG_AUDIO` | `0` | `1` = 每次识别往 `debug_audio/` 存一份 wav（原项目默认存，已积 178MB） |

配置有三个来源，**都写成 `:=` 语义（只补空缺），所以先读到的优先**：

```
① 调用方已 export 的环境变量        最高（临时试验用）
② tools/audio_stt/asr.env          本机专属，已 gitignore，不进库
③ <仓库根>/asr.env                  共享默认，进库
④ 脚本内置默认                     最后兜底
```

为什么这么分：仓库根那份跟着 git 走，改它等于改所有人的默认值。而「本机要不要
对外监听」「这台机器模型放哪」是环境属性，不是项目属性 —— 188 是内网机器可以绑
`0.0.0.0`，笔记本不能。分层才两头都对。

直接用 python 跑入口（不经脚本）时不会自动加载任何文件，
需要 `set -a; . ./asr.env; set +a` 或由 pm2/systemd 注入。

## 谁在用

- `webtools/server/app/asr.py` — BFF 侧的 ASR 通道，转发到 `audio_service_server.py`（:8888）。
  前端永远只认 `POST /v1/audio/transcribe`，换端口/换机器不用重新打包前端。
- 内网其它机器 — 直接连 `ws://<host>:8888/ws/audio/audio2text/{client_id}`（如 188 已绑 `0.0.0.0`）。

## 迁移改动（与原项目的差异，逐条）

1. **去掉 `common.log.logservice` / `config.cfg.Syscfg`**
   `audio_service_server.py` 里 `cfg = Syscfg()` 之后**从未使用** `cfg`，但 `Syscfg()`
   构造要求 `APP_ROOT_DIR` + `config/config.json` 就位。为一行无用代码把服务拴在别人的
   配置体系上，故移除，改用标准 `logging`（`_logging.py`）。
2. **`from reasoning_service.audio.audio_decode import ...` → 同目录导入**
   带 `try/except` 双写，既能 `python xxx.py` 直跑，也能 `from tools.audio_stt.xxx import`。
3. **WS 服务默认绑 `127.0.0.1`**（原写死 `0.0.0.0`），并支持 `--host/--port`。
4. **`uvloop.install()` 改可选**：uvloop 不支持 Windows，原来无条件调用会直接起不来。
5. **调试音频默认关闭**（`ASR_DEBUG_AUDIO=1` 才存）：原代码每识别一次写一个 wav，
   原项目已积 178MB / 417 个文件。
6. **删除旧版 `audio_service.py`**：它与 `audio_service_server.py` 功能重叠（同一模型、
   同一套参数），但模型写死 `device="cuda:0"`（在 macOS 上必然构造失败）、`logger(...)`
   当函数调（必定 `TypeError`）。曾在修好后标“旧版保留供参考”，2026-11-07 删除 ——
   全仓无人引用，留着只能让人问“到底跑哪个”。原样行为完整保留在 git 历史里。
7. **未迁入**：`debug_audio/` 下 417 个 wav（178MB）、`audio_service_server.py.bak_*`。
   前者是调试录音，后者是历史备份 —— 都不属于代码。

## 启停脚本（在**仓库根目录**，不在本目录）

两套，按是否交给进程管理器选：

| 场景 | 命令 |
|---|---|
| **人工启停**（临时跑一会） | `./asr_service.sh start\|stop\|restart\|status\|log` |
| **开机自启 / 挂了自动拉起** | `pm2 start ecosystem.config.js` → `pm2 save` → `pm2 startup systemd` |

交给 PM2 后**不要再用脚本 stop** —— kill 掉 python，PM2 会按 autorestart 立刻拉起来，
反复 stop 反复起。脚本已做识别（父进程是 PM2 就拒绝动手并提示用 `pm2 stop`）。

`asr_service.sh run` 是前台模式（不写 PID 文件、直接 `exec python`），PM2 清单用这个；
它同样读 `asr.env` 三层配置，所以 PM2 里跑的和你手动跑的是一套参数，不是两套。

来源 `motozolo_gpt_tools/asr_service.sh`，只改「从哪取值」，不改「怎么启停」。
放在仓库根是为了少跳一层目录：

```bash
./asr_service.sh start|stop|restart|status|log
```

| 原脚本 | 现在 |
|---|---|
| `APP_DIR` 写死 `~/my-project/...` | 脚本自身所在目录（`BASH_SOURCE`），在哪个 cwd 执行都一样 |
| `PYTHON` 写死 conda 路径 | 探测顺序：`$ASR_PYTHON` → 仓库 `.venv`（能 `import funasr` 才算）→ PATH 上的 `python3` |
| `ENTRY` 写死 `reasoning_service/audio/...` | `tools/audio_stt/audio_service_server.py`（`$ASR_ENTRY` 可覆盖） |
| `source env_motozolo.sh`（19 行，其中 17 行是 motozolo 业务变量） | 仓库根 `asr.env`（进库，模型路径显式可见） |
| 日志/PID 落项目 `logs/` | 落仓库根 `logs/`（`asr_server.log` / `asr_server.pid`，已 gitignore） |

**两处行为修正**（原脚本会出事，不是风格问题）：

1. **`stop` 只杀自己的进程**。原版 `lsof -ti :8888 | xargs kill` —— 8888 是
   极常见的开发端口，被别的服务占了就误杀。现在先比对 `ps -p <pid> -o command=`
   是否含本入口文件名；不是就**列出来但不动**，退出码 1。
2. **`start` 区分「已在运行」与「端口被别人占了」**。原版只判「端口有人听」，
   于是会对着别人的进程说「ASR 已在运行，请用 restart」—— 而 `restart`
   正是误杀的入口。

解释器找不到时明确报错并给修复命令，**不在 import 阶段半途死掉**：

```
❌ 找不到装有 funasr 的 Python 解释器。
   在本仓库根目录执行:  uv sync --extra audio
   或显式指定:        ASR_PYTHON=/path/to/python ./asr_service.sh start
```

验证（临时端口 8906，不碰线上 8888，均在仓库根执行）：

| 项 | 结果 |
|---|---|
| 自动探测（.venv 尚未装 funasr） | 明确报错 + 修复命令，退出码 1 |
| `ASR_PYTHON=<conda> start` | 起在 8906，✅ 已启动 |
| WS `/ws/audio/audio2text` 送 AIFF | `今天天气不错，我们下午去公园散步吧。` |
| 日志落点 | 仓库根 `logs/asr_server.log`（不是本目录） |
| `stop` | 端口释放、PID 文件清理 |
| 陌生进程占 8905 后 `stop` | 拒绝动手，退出码 1，陌生进程存活 |
| cwd 切到 `/tmp` 执行 `status` | 仍能正确定位自身与代码目录 |
