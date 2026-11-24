# Let an Agent install AgentGeeX by itself

> This document is written for **an Agent already running on your machine** to execute. It is not prose for humans to read.
> A human only needs to do two things: **hand the document to the Agent**, and **type the database password once** when the Agent asks for it.

---

## The one-line instruction for humans

Send the block below to your Agent verbatim (OpenClaw / Claude Code / Codex / any Agent that can read files and run commands):

```
请读取并严格执行 https://gitee.com/robertrfeng/agentgeex 仓库里的
docs/getting-started/install-with-agent.md。按步骤推进，每步先验证再走下一步。
遇到需要密码的地方停下来告诉我，我自己填，你不要代填、不要打印。
```

> In English, that block instructs the Agent to read and strictly follow `docs/getting-started/install-with-agent.md`
> from the `https://gitee.com/robertrfeng/agentgeex` repository, to advance step by step verifying each one first,
> and to stop and ask the human wherever a password is needed rather than filling it in or printing it.

---

## Three iron rules for the Agent

| # | Iron rule | Why |
|---|---|---|
| **1** | **Never request, never fill in, never print any password or token.** When a password is needed, give the human the path of the template file and let them write the value in themselves | A password that enters the conversation log is already leaked. Credentials for this project exist only in `~/.agx/agx_creds.env` (mode 600) |
| **2** | **Never overwrite an existing installation.** If `~/.agx` already exists and is non-empty, stop and ask the human: overwrite / keep / use a different directory | Overwriting wipes other Agents, their tasks and their memory |
| **3** | **Verify each step before moving to the next.** Every step below states "what you should see". If you do not see it, stop and report — do not skip ahead | The cost of skipping one step is an error ten steps later, and the error message points in the wrong direction |

Note: **all commands run from the repository root**. Every `./agx.sh`, `./mcp.sh` and `scripts/...` below is relative to the repository root.

---

## Step 0 · Probe the environment (the only basis for judgement)

```bash
uv run python3 scripts/probe_env.py --json
```

This is the **only** sanctioned way to determine "what is on this machine". **Do not improvise your own checks such as `which` / `node -v` / `brew list`** — every Agent would test differently and reach a different conclusion.

> **Why `uv run python3` rather than a bare `python3`:** the project requires Python ≥ 3.12, while the `python3` shipped with Ubuntu 20.04 is 3.8 and fails immediately with `unsupported operand type(s) for \|`. `uv run` picks up the interpreter named in `.python-version` (currently 3.12) — the same one `agx.sh` uses.

The output has two key fields:

```json
{ "ready": false, "missing_for_core_install": ["uv", "mysql (connectable)", "redis (connectable)", "openclaw"] }
```

- `ready: true` → jump straight to **Step 4**
- Whatever appears in `missing_for_core_install` is what you install (Steps 1–3)

For a human-readable view, drop `--json`:

```bash
uv run python3 scripts/probe_env.py
```

---

## Step 1 · Install the system dependencies

Install each item that appeared in `missing_for_core_install`, using the table below. **Install only what is missing; do not reinstall what is already present.**

### 1.1 `uv` (Python environment manager, required)

| System | Command |
|---|---|
| macOS | `brew install uv` |
| Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows | `winget install --id=astral-sh.uv` |

> If `uv` is still not on PATH afterwards: it usually lands in `~/.local/bin/uv` or `~/.cargo/bin/uv`; add that directory to PATH.

### 1.2 `git` (required, usually already present)

| System | Command |
|---|---|
| macOS | `xcode-select --install` or `brew install git` |
| Debian/Ubuntu | `sudo apt install -y git` |
| RHEL/CentOS | `sudo dnf install -y git` |
| Windows | `winget install --id=Git.Git` |

### 1.3 Python ≥ 3.12 (required)

Only needed when `python.ok` in `probe_env.py` output is `false`.

| System | Command |
|---|---|
| macOS | `brew install python@3.12` |
| Debian/Ubuntu | `sudo apt install -y python3.12 python3.12-venv` |
| Windows | `winget install --id=Python.Python.3.12` |

> Actual execution is managed by `uv`; there is nothing to `pip install` by hand.

### 1.4 Node.js + npm (required for core nodes)

The version is determined by **OpenClaw's own `engines` field** — do not guess. Current OpenClaw requirement:

```
>=22.22.3 <23  ||  >=24.15.0 <25  ||  >=25.9.0
```

Check the requirement of the target OpenClaw version first (`node -p "require('openclaw/package.json').engines"` once OpenClaw is installed), then choose the Node version.

| System | Command |
|---|---|
| macOS | `brew install node` |
| Debian/Ubuntu | Use nvm (recommended, makes version switching easy): `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh \| bash` then `nvm install 22` |
| Windows | `winget install --id=OpenJS.NodeJS.LTS` |

> A pure **worker node** (`./agx.sh --host`) does not need Node.

---

## Step 2 · No Agent runtime yet? Install one first

AgentGeeX is an orchestration layer, so **there must be a runtime underneath that actually does the work**. Decide in this order:

| Situation | Action |
|---|---|
| You (the Agent reading this document) are already OpenClaw / Claude Code / Codex / DSH | **Skip this step** — you are the runtime |
| The machine has none of the above | Install **OpenClaw** (first-class citizen, the most complete feature set) |

### Installing OpenClaw

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

or:

```bash
npm i -g openclaw@latest --allow-scripts=openclaw
```

**What you should see**:

```bash
openclaw --version     # → OpenClaw <version> (<build>)
```

> ⚠️ After installing OpenClaw, **re-run `probe_env.py`** — it may have changed the Node version as a side effect.

### Initialise and start the gateway (first time only)

Installing only gives you a command; `~/.openclaw` does not exist yet — and Phase 4 of AgentGeeX needs the gateway to register Agents.

```bash
openclaw setup --baseline          # create ~/.openclaw (non-interactive, does not touch model config)
openclaw gateway install           # install as a resident service (launchd / systemd)
openclaw gateway status            # → running
```

To try it in the foreground first without installing a service:

```bash
openclaw gateway                   # run in the foreground; Ctrl-C to exit
```

**What you should see**: `openclaw gateway status` reports running, and port 18789 is listening.

> What happens if you skip this: `./agx.sh` fails at Phase 4 with "cannot connect to the OpenClaw gateway ws://127.0.0.1:18789"
> and lists the three commands above. The installation is **not aborted** (the remaining phases still run), but the four system Agents
> are never registered — the machine boots but cannot take work. Complete these three steps and re-run; nothing is created twice.

### Status of the other runtimes (measured 2026-11)

| Runtime | Status | Notes |
|---|---|---|
| OpenClaw | First-class | Sessions / wake-ups / receipts fully supported |
| Claude Code | Supported | Through a separate CLI channel |
| DSH (DeepSeek Harness) | Supported | Long-lived stdio JSON-RPC child process |
| Codex | **Not supported** | POC only; do not promise it as "supported" |

---

## Step 3 · MySQL and Redis

AgentGeeX uses MySQL as its persistence layer and Redis as its speed layer; **both must be connectable**. Pick one of three paths:

### 3.1 Already present on the machine (most common)

Confirm the account can connect:

```bash
mysql -h 127.0.0.1 -P 3306 -u <user> -p -e "SELECT VERSION();"
redis-cli -h 127.0.0.1 -p 6379 -a '<password>' ping     # → PONG
```

If that works, skip to **Step 4**. **Do not touch the existing databases or instances** — AgentGeeX only creates its own database.

### 3.2 Run a local instance with the system package manager

| System | MySQL | Redis |
|---|---|---|
| macOS | `brew install mysql && brew services start mysql` | `brew install redis && brew services start redis` |
| Debian/Ubuntu | `sudo apt install -y mysql-server && sudo systemctl start mysql` | `sudo apt install -y redis-server && sudo systemctl start redis-server` |
| Windows | `winget install --id=Oracle.MySQL` | Use WSL2 or Docker |

After installing, set a password for MySQL's root first (`mysql_secure_installation`), and record the password in the credentials file from Step 4.

### 3.3 Use Docker (least effort, also the easiest to clean up)

```bash
docker run -d --name agx-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD='<your own password>' \
  -v agx_mysql:/var/lib/mysql mysql:8

docker run -d --name agx-redis -p 6379:6379 \
  -v agx_redis:/data redis:7
```

> The password must be **entered by a human**, or supplied through a one-shot environment variable. The Agent must not write it into logs, shell history or the conversation.

### Verify (all three paths must do this)

```bash
uv run python3 scripts/probe_env.py --json
```

Check that `services.mysql.connected` and `services.redis.connected` are both `true`. If they are not, do not continue.

---

## Step 4 · Fetch the code and write two configuration files

**First clone the code into a directory the user can write to.** The repository root becomes the working directory for every command that follows.

### 4.1 Directory check (iron rule 2)

```bash
ls -la ~/.agx 2>/dev/null
```

- `~/.agx` **does not exist** → continue
- `~/.agx` **exists and is non-empty** → **stop and ask the human**:
  - Overwrite and reinstall → `./agx.sh --force`
  - Keep the existing files → `./agx.sh` (it will ask during Phase 3)
  - Use another directory → `./agx.sh --workdir <new-directory>`

### 4.2 Write `~/.agx/agx_config.json` (identity and addresses, no passwords)

**Do not hand-write a minimal configuration — copy the template from the repository and change three places.**

```bash
mkdir -p ~/.agx
cp agx_config.json.example ~/.agx/agx_config.json
chmod 600 ~/.agx/agx_config.json
```

Then change these three places to the real values of this machine:

| Field | Change to |
|---|---|
| `host_id` | A unique identifier for this machine, e.g. `test_host_mac` / `test_srv_a` (**required**; Phase 1c exits immediately without it) |
| `gateways[0].host_id` | The same value as `host_id` above |
| `gateways[0].token` | The gateway token of your local OpenClaw (read with the next command) |

Reading the token — **mind who you are**:

```bash
# In an interactive terminal: it can be printed directly
openclaw gateway auth-token --show

# Agent (non-interactive environment): the command above refuses to print, so read the config directly
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')))['gateway']['auth']['token'])"
```

> `--show` reports `Refusing to print the Gateway token outside an interactive terminal`
> in a non-interactive environment — that is OpenClaw's security design, not a fault. Agents take the second command and write the value into
> `gateways[0].token` of `~/.agx/agx_config.json`, **without printing it into the conversation**.

> **Why copying the template matters more than hand-writing:** besides `host_id`/`mysql`/`redis`, the template also carries the
> `daemon` (watchdog timeouts, poll interval), `ws` (reconnect and concurrency), `mcp` and `dsh` sections.
> A hand-written minimal configuration is missing those — measured on a blank machine on 2026-11-23: without the `daemon` section,
> `agx_daemon.py` crashes on startup at `config.daemon.get(...)`, which looks like "the installation finished but
> no process is running". That spot has since been changed to fall back to defaults with a warning, but **filling in from the template remains the right way**.

> `gateways[].ws_url` is the address of the OpenClaw gateway; use whatever port your machine actually listens on (default 18789).

### 4.3 Credentials file `~/.agx/agx_creds.env` (**only a human may fill it in**)

The Agent stops here: **generate an empty template with mode 600, then ask the human to fill in the values themselves.**

```bash
umask 077
cat > ~/.agx/agx_creds.env <<'EOF'
AGX_DB_HOST=127.0.0.1
AGX_DB_PORT=3306
AGX_DB_USER=
AGX_DB_PASS=
AGX_DB_NAME=agentgeex
AGX_REDIS_HOST=127.0.0.1
AGX_REDIS_PORT=6379
AGX_REDIS_DB=0
AGX_REDIS_USERNAME=default
AGX_REDIS_PASSWORD=
EOF
chmod 600 ~/.agx/agx_creds.env
```

Then say to the human:

> Please fill in `AGX_DB_USER` / `AGX_DB_PASS` / `AGX_REDIS_PASSWORD` in `~/.agx/agx_creds.env`, and tell me when you are done. **Do not send them to me.**

**Verify**:

```bash
uv run python3 scripts/probe_env.py --json
```

`config.agx_creds.env.mode_ok` must be `true` (=600) and `services.mysql.connected` must be `true`.

---

### 4.4 Models: you configure them yourself, AgentGeeX does not bind to any

AgentGeeX does not touch models. Which provider, which model, where the key lives — all of that belongs to OpenClaw.
**It is not part of the installation steps and does not need to be told to AgentGeeX** (the installer neither reads, stores nor validates model configuration).

One thing you must know, though: when the model is not configured, Agents still receive tasks but execution fails with

```
No route-compatible authentication source is configured
```

That is an OpenClaw error, not an AgentGeeX one. Configure any provider and key inside OpenClaw
(the official `openclaw` documentation covers it fully), then run this self-check:

```bash
openclaw agent --agent main -m "只回答两个字：通了"    # → 通了
```

> The command asks the model to answer with two characters meaning "it works"; a reply means the model path is live.

If it answers, the model is working and AgentGeeX can do real work. Cannot configure it? Skip for now and do it after installing —
the installation flow does not depend on it, the installed Agents simply cannot do work yet.

---

## Step 5 · Install the kernel

```bash
./agx.sh
```

| Scenario | Command |
|---|---|
| Interactive (asks for confirmation) | `./agx.sh` |
| Non-interactive, all defaults | `./agx.sh --default` |
| Worker node only (no database, no daemons) | `./agx.sh --host` |
| Preview what would happen, change nothing | `./agx.sh --dry-run` |
| Overwrite and reinstall | `./agx.sh --force` |
| Custom working directory | `./agx.sh --workdir ~/somewhere` |

**What you should see: ten phases passing in order**:

```
Phase 1    配置确认
Phase 2    依赖检测
Phase 3    工作目录设置
Phase 4    注册 Agent
Phase 5    文件同步与依赖下发
Phase 6    数据库初始化
Phase 6.5  结构同步
Phase 7    本机初始化
Phase 7.5  Redis 缓存重建
Phase 8    清理与启动
```

> Those labels are printed verbatim by `install.py` (`log_step("Phase 2: 依赖检测")` and so on), so they stay in the original
> language — translating them here would make the document disagree with what the terminal shows.

If any step prints `[ERROR]` → **stop**, then consult "Troubleshooting quick reference" at the end of this document.

A completed install leaves four system Agents on this machine:

| Agent | Role | Responsibilities |
|---|---|---|
| `agx_core` | workshop_manager | Scheduling hub |
| `agx_host` | host_manager | Host management, Agent deployment |
| `agx_agentfather` | agent_manager | **The only way to create an Agent** |
| `agx_projectmother` | project_manager | **The only way to create a project** |

---

## Step 6 · Start the MCP gateway

```bash
./mcp.sh start
./mcp.sh check
```

**What you should see**:

```
[gateway] selfcheck OK   http://127.0.0.1:31890/agentgeex/mcp
```

Three endpoints (`agentgeex` with 100 tools / `mysql-srv` with 5 tools / `redis-srv` with 7 tools) share one process:

```
http://127.0.0.1:31890/agentgeex/mcp
http://127.0.0.1:31890/mysql/mcp
http://127.0.0.1:31890/redis/mcp
```

Other subcommands: `./mcp.sh status` / `stop` / `restart` / `logs` / `run` (foreground).

> Production runs it under PM2; `mcp.sh` is the fallback launcher (for when PM2 is broken, or for a one-off debug session where you want the full output).

---

## Step 7 · Register the MCP server with your runtime

### 7.1 OpenClaw

Edit `~/.openclaw/openclaw.json` and add the three entries under `mcp.servers`, **adding only — never removing**:

```json
{
  "mcp": {
    "servers": {
      "agentgeex": { "transport": "streamable-http", "url": "http://127.0.0.1:31890/agentgeex/mcp" },
      "mysql-srv": { "transport": "streamable-http", "url": "http://127.0.0.1:31890/mysql/mcp" },
      "redis-srv": { "transport": "streamable-http", "url": "http://127.0.0.1:31890/redis/mcp" }
    }
  }
}
```

Back up first: `cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak_$(date +%Y%m%d_%H%M%S)`
Then restart OpenClaw.

**What you should see**: tools whose names start with `agx_*` appear in the tool list (100 in total).

### 7.2 Claude Code

```bash
claude mcp add -s user --transport http agentgeex  http://127.0.0.1:31890/agentgeex/mcp
claude mcp add -s user --transport http mysql-srv http://127.0.0.1:31890/mysql/mcp
claude mcp add -s user --transport http redis-srv http://127.0.0.1:31890/redis/mcp
```

### 7.3 Other MCP clients

Any client that supports `streamable-http` just needs the three URLs above. Tool names are unchanged (`agx_*`).

---

## Step 8 · Acceptance (all four must pass)

### 8.1 Kernel self-check

Tell your Agent: **"run `agx_check_services`"**

**What you should see**: MySQL `healthy`, Redis `healthy`.

### 8.2 Create a project

Say: **"create a test project named install_test with abbreviation tst"**

**What you should see**: a `project_id` comes back (of the form `proj_xxxx`).

> Creating a project must go through `agx_projectmother`. If the Agent is rejected when calling `agx_create_project` directly, that is the permission design working, not a bug.

### 8.3 Create an Agent

Say: **"create a worker for this project and deploy it to this machine"**

**What you should see**: `agx_agentfather` clones and `agx_host` deploys; the Agent list gains one entry.

### 8.4 Dispatch the first piece of work

Say: **"have it write a 300-word test note and put the output in outputs"**

**What you should see**: the `execution` goes from `running` to `succeeded`, and the output file appears in that Agent's workspace.

All four passed = the installation is good. **If 8.1 fails, do not proceed to 8.2** — fix the connection first.

---

## Troubleshooting quick reference

| Symptom | Check this first | Common cause |
|---|---|---|
| `uv sync` hangs while downloading (e.g. `pydeck` / `streamlit`) | `curl -m 8 -o /dev/null -w '%{http_code}' https://pypi.org/simple/` | Direct connection to pypi times out from mainland China. Switch the mirror and re-run:<br>`UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ ./agx.sh` |
| `Phase 1a` reports `未找到 agx_config.json` (agx_config.json not found) | `ls ~/.agx/agx_config.json` | The file is not in `~/.agx/`, or `$AGX_CONFIG_PATH` points elsewhere |
| `Phase 1c` reports `未配置 host_id` (host_id not configured) | Check `host_id` in the json | Empty value or a typo |
| `Phase 2` reports `依赖缺失: uv` (missing dependency: uv) | `which uv` | uv is installed under `~/.local/bin` but PATH was not loaded (common with nvm / non-interactive shells) |
| `Phase 6` reports `MySQL 连接失败` (MySQL connection failed) | `uv run python3 scripts/probe_env.py --json`, look at `services.mysql` | Wrong password / service not started / `bind-address` allows localhost only while you connect remotely |
| Redis unreachable | Same as above, look at `services.redis` | Wrong password, `requirepass` differs from the credentials file, or `protected-mode` blocks it |
| `Phase 4` reports "cannot connect to the OpenClaw gateway ws://127.0.0.1:18789" | `ls ~/.openclaw` + `openclaw gateway status` | OpenClaw is installed but **never initialised / never started**. `openclaw setup --baseline` → `openclaw gateway install` → `openclaw gateway auth-token --show` into `gateways[0].token` of `agx_config.json`, then re-run the installer |
| `Phase 4` warns `AGX_GATEWAY_TOKEN 未设置` (AGX_GATEWAY_TOKEN not set) | `openclaw gateway auth-token --show` | `gateways[0].token` in `agx_config.json` is empty; WS registration needs it |
| `Phase 7` fails to register Agents | `openclaw --version` plus whether the gateway runs | WS protocol version mismatch; you can add `--skip-agent-register` and use `openclaw agents add` instead |
| `mcp.sh check` fails on an endpoint | `./mcp.sh logs` | Port 31890 is taken, or a bad DB configuration prevents MCP from starting |
| The Agent cannot see `agx_*` tools | Client configuration and a restart | The client was not restarted, or `mcp.servers` is nested wrongly (it must be `mcp.servers`, not a top-level `mcpServers`) |
| Tools list fine but calls report a permission error | `agx_get_global_config` to see your own `agent_role` | The role is `worker`: creating projects / Agents is supposed to be refused |
| A dispatched task never moves | `agx_running_tasks` + `./mcp.sh logs` | The daemon is not running; `agx.sh --wakeup` can wake `agx_core` |
| Agent execution reports `No route-compatible authentication source is configured` | `openclaw agent --agent main -m test` | No model configured inside OpenClaw (see 4.4). An OpenClaw configuration issue, unrelated to AgentGeeX |
| Gateway is `active` but 18789 is not listening | `journalctl --user -u openclaw-gateway -n 40` | Read the first error in the log. If it is plugin-related, follow the hint with `openclaw plugins ...` (an OpenClaw-side problem) |
| `uv sync` hangs or dependency downloads time out | The domain in the error message | Network issue. You can temporarily switch the index (`UV_DEFAULT_INDEX`) and switch back afterwards |
| "Installation finished" but `daemon.sh status` says it is not running | `tail -30 logs/*.out` | Look for `[config] 缺少配置段 'xxx'` (missing configuration section): a hand-written minimal config lacks the `daemon`/`ws` sections. Fill them in from `agx_config.json.example` |
| Remote MySQL connections hang without returning (local works) | Look for `could not be resolved` in the mysql error log | The server has `skip_name_resolve=OFF` and no internal DNS, so reverse lookups time out. Add the client IP to `/etc/hosts`, or set `skip_name_resolve=ON` |

---

## Uninstall and reinstall

```bash
./agx.sh --uninstall      # removes every registered Agent and the install directory
```

> ⚠️ This is a **destructive** operation: it deletes Agent registrations, task records and the working directory. A human must confirm before it runs.
> The database is not dropped automatically; that needs a manual `DROP DATABASE` (which is also a form of protection).

---

## Appendix: completion criteria for the Agent (self-check list)

When you believe the installation is done, check every line below; report completion only when every answer is "yes":

- [ ] `uv run python3 scripts/probe_env.py --json` → `ready: true`
- [ ] `./agx.sh` ran through all ten phases
- [ ] All four system Agents exist (`agx_core` / `agx_host` / `agx_agentfather` / `agx_projectmother`)
- [ ] `./mcp.sh check` → selfcheck OK on all three endpoints
- [ ] `agx_*` tools (100 of them) appear in the client's tool list
- [ ] `agx_check_services` → MySQL healthy + Redis healthy
- [ ] A project was created, an Agent was created, and one `succeeded` execution went through end to end
- [ ] Nowhere in the process did anyone paste a password into the conversation

The last item is not a formality: **once a password has been in a conversation, that installation is already insecure and the password must be changed.**
