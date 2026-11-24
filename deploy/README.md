# deploy/ — AgentGeeX 守护进程 systemd 配置

## 适用场景

每台跑 daemon 的主机（`--core` 模式：daemon + watchdog + ws_client）。
「一台机器一只狗」拓扑下，**每台都要装**（只有核心主机需要看门狗，
但 `--core` 已包含，统一用 `--core` 更简单；不需要看门狗用 `--host`）。

## 安装（user 级 systemd，无需 sudo）

```bash
mkdir -p ~/.config/systemd/user
cp agx-daemon.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now agx-daemon

# 关键：开启 linger，否则用户不登录时不会启动
loginctl enable-linger "$(whoami)"
```

## 验证

```bash
systemctl --user is-active agx-daemon      # → active
systemctl --user is-enabled agx-daemon     # → enabled
systemctl --user show agx-daemon -p MainPID --value   # daemon 主进程 PID

# 进程口径：core 模式共 4 个脚本进程
#   agx_daemon.py（systemd 的 MainPID）
#   ├─ agx_watchdog.py
#   ├─ agx_ws_client.py
#   └─ agx_dsh_client.py
ps -eo pid,ppid,command | grep -E "agx_(daemon|watchdog|ws_client|dsh_client)\.py" | grep -v grep

# 自动重启验证（关键：确认不是「看起来活着」）
OLD=$(systemctl --user show agx-daemon -p MainPID --value)
kill -9 "$OLD"
sleep 25
NEW=$(systemctl --user show agx-daemon -p MainPID --value)
[ "$OLD" != "$NEW" ] && echo "✅ 自动拉起生效"
```

## 为什么是 Type=simple 而不是 forking/oneshot

`daemon.sh start` 内部会 `setsid` 脱离进程组（防 gateway 重启连坐杀死），
systemd 无法跟踪这个子进程。三种类型都实测过：

| Type | 结果 |
|---|---|
| `forking` + PIDFile | PIDFile 是 `{host_id}.pid`，不是 systemd 预期的格式，误判 |
| `oneshot` + RemainAfterExit | **最危险**：`kill -9` 主进程后 `is-active` 仍是 `active`，只剩 watchdog/ws 两个孤儿。服务看起来健康，实际已瘫 |
| `simple` + `daemon.sh run` | ✅ 主进程即服务主进程，进程死即服务死，`Restart=always` 真正生效 |

所以本配置配套 `daemon.sh run`（前台运行，不 setsid）。
停止仍走 `daemon.sh stop`，由它清理 watchdog/ws/dsh 子进程。

## 日志

```
journalctl --user -u agx-daemon -f          # 实时
journalctl --user -u agx-daemon --since today
~/.agx/logs/{host_id}.out                        # daemon 自己的日志（仍在写）
```

## 常见问题

**启动报「找不到可用解释器」**
`daemon.sh` 需要 Python >= 3.10。它会依次尝试：
`$AGX_PYTHON` → `~/.agx/.venv` → `$AGX_SOURCE_DIR/.venv` →
同机常见源码目录（`~/agentgeex/agx` / `~/project/agx` 等）→
`uv run` → 系统 python3。

若都不可用，在 `~/.agx` 建 venv，或设环境变量：
```bash
# 方式一：在 unit 里加
Environment=AGX_SOURCE_DIR=/home/user/agentgeex/agx

# 方式二：直接指定解释器
Environment=AGX_PYTHON=/home/user/agentgeex/agx/.venv/bin/python
```

**为什么优先源码 venv 而不是 uv run**
`uv run` 在受限网络（GitHub 被墙）下会卡在下载 CPython，
进程只剩 uv 包装器、日志 0 字节、永不超时。实测 118 踩过。
同机有 venv 时永远比 uv run 可靠。
