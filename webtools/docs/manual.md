# AgentGeeX Console · Operations Manual

> This manual is about **using the web interface**: how to start it, how to sign in, what each
> page does, and how to run one piece of work end to end.
> Installing AgentGeeX itself (kernel, database, daemons) is a different document --
> see [`../../docs/getting-started/install-with-agent.md`](../../docs/getting-started/install-with-agent.md).
>
> Screenshots come in both languages (the UI language switch is at the top right). Every one of them
comes from a real installed test machine: host `test_srv_a`, network
> `lan_test`, one auto-created `system` project, 12 agents. Your names will differ; the screens
> will look the same.

---

## Contents

1. [What this is](#1-what-this-is)
2. [Before you start](#2-before-you-start)
3. [Starting up](#3-starting-up)
4. [Signing in](#4-signing-in)
5. [Interface map](#5-interface-map)
6. [Page by page](#6-page-by-page)
7. [One full walkthrough: dispatch a task](#7-one-full-walkthrough-dispatch-a-task)
8. [Voice input (speech to text)](#8-voice-input-speech-to-text)
9. [Troubleshooting](#9-troubleshooting)
10. [Stopping and reinstalling](#10-stopping-and-reinstalling)

---

## 1. What this is

The AgentGeeX console is a **web UI for people**.

Four layers sit underneath it:

```
Browser (what you see)
   │  REST + JWT
   ▼
BFF (FastAPI, port 8090)        ← auth, aggregation, field trimming, rate limiting
   │  MCP protocol (streamable-http)
   ▼
MCP gateway (port 31890)        ← the kernel's only entry point, 96 tools
   │
   ▼
MySQL + Redis                   ← single source of truth + cache
```

**Why the BFF layer exists**: the kernel is built for agents (MCP, 96 tools, raw wide rows).
A person wants "12 agents, 1 host, 7 tasks today". The BFF turns wide rows into cards, hides
internal fields, and keeps authorisation on the server. **The frontend never talks to MySQL.**

One sentence: **every number you see in the UI was fetched from the kernel through MCP.**

---

## 2. Before you start

Three things must be running:

| Component | Notes | How to check |
|---|---|---|
| MCP gateway `:31890` | the kernel's only entry point | `curl -s http://127.0.0.1:31890/agentgeex/mcp` responds |
| MySQL + Redis | the kernel's data layer | if the kernel starts, these are fine |
| Node ≥ 18 | frontend build and dev server | `node -v` |
| Speech service (optional) | speech to text. Without it the mic button stays unavailable; everything else works | see chapter 8 |

If AgentGeeX itself is installed per the install guide (`~/.agx` exists, `daemon.sh status`
reports running), that is enough.

> **First run on a fresh machine** needs the frontend dependencies once:
>
> ```bash
> cd ~/.agx/webtools/client && npm install
> ```
>
> On mainland networks set a mirror first: `export npm_config_registry=https://registry.npmmirror.com`

---

## 3. Starting up

One command brings up all three layers:

```bash
cd ~/.agx/webtools
./start.sh start
```

Success looks like this:

```
🚀 启动 Win3 商业控制台（三层）
✅ MCP 网关已在运行 (:31890)
✅ BFF 就绪 (:8090)   日志: /tmp/win3_bff.log
✅ 前端就绪 (:5190)   日志: /tmp/win3_client.log
✅ 全部就绪 → 打开 http://localhost:5190/
```

**Which log to read when a layer breaks:**

| Layer | Port | Log | Symptom when down |
|---|---|---|---|
| MCP gateway | 31890 | `~/.agx/logs/agx-mcp-gateway.out` | every list is empty; `/readyz` shows `mcp_sse: false` |
| BFF | 8090 | `/tmp/win3_bff.log` | pages load but data never arrives; browser console full of 502s |
| Frontend | 5190 | `/tmp/win3_client.log` | the page will not open at all |

**Common subcommands:**

```bash
./start.sh status     # three-layer status and ports
./start.sh restart    # restart BFF and frontend (leaves MCP alone)
./start.sh stop       # stop BFF and frontend (MCP is PM2's job; this script will not touch it)
./start.sh logs       # follow logs
./start.sh check      # end-to-end health check
```

**Changing ports** (when something already occupies the defaults):

```bash
BFF_PORT=8190 CLIENT_PORT=5290 ./start.sh start
```

> ⚠️ **`start.sh` must run in a shell that has `node` on PATH.** Launch it from systemd, cron,
> or a non-interactive ssh session and `npm` may be missing -- the frontend layer then fails
> with `nohup: 无法运行命令 'npm'` (cannot run npm).
> Fix: prepend the node directory, e.g. `export PATH=/path/to/node/bin:$PATH`.

---

## 4. Signing in

Opening `http://localhost:5190/` shows the sign-in page first:

![Sign-in](./screenshots/en-US/01_login.png)

> ⚠️ **Sign-in is currently a demo-grade implementation**: `authenticate()` in
> `server/app/auth.py` is a placeholder -- **any non-empty username and password are accepted**.
> Its only job is to issue a JWT and record you as the **audit subject** (who clicked).
> Wiring a real user source (a user table, or your corporate IdP) is a must-do before release;
> the code comments mark exactly where to plug it in.

Two concepts that are easy to confuse (this is the one place the product design invites you to
get backwards):

| Concept | What it is | Where you choose it |
|---|---|---|
| **Login account** | a **person** (operator) | at sign-in |
| **Sender agent** | which agent this action speaks **as** | per action, in the dialog |

After signing in, the top-right corner shows the name you signed in with.

---

## 5. Interface map

You land on **System status**. The left rail has 14 entries in four groups:

| Group | Menu | Purpose |
|---|---|---|
| **System** | System status | the whole picture at a glance: hosts, projects, plans, agents, tasks, sessions |
| | Quick access | your starred projects / agents / sessions / plans |
| | Compute hosts | the machine inventory |
| **Agent management** | Agents | every agent; dispatch work, read its counters |
| | Sessions | agent conversations, with context-watermark |
| | Tasks | the task template catalogue (what an agent *can* do) |
| | Executions | every real run: who sent it to whom, how long, did it work |
| | Quick commands | pre-built commands -- fill a few parameters and send |
| | Shared skills | the global skill library (distilled "how to") |
| | Notices / work logs | announcements and logs written by agents |
| **Project management** | Projects | projects and their members |
| | Project groups | reusable team templates |
| **Plans** | Plans | multi-step plans and their progress |

Persistent controls along the top:

- **Send message** (blue button, top right): global dispatch, available on every page
- **Tab bar**: pages you opened stay as tabs
- **Service settings**: pick the speech host, check the MCP channel (see 6.14)
- **Fullscreen / theme (light-dark) / language (zh-en) / lock screen / user menu**

---

## 6. Page by page

### 6.1 System status

![System status](./screenshots/en-US/02_system.png)

Answers "what shape is my system in right now" on one screen.

- **Hosts**: how many, how many online, total agents / sessions / executions / slots. Each
  card names the host (`test_srv_a` and friends) and its OS.
- **Project overview**: active / archived / total, plus an active-share bar.
- **Plan execution**: running / completed / cancelled / draft / cancelling / archived, and the
  next scheduled plan.
- **Agent status**: online / busy / offline / error.
- **Executions (last 24h)**: queued (scheduled / timed) / running / awaiting human confirm /
  succeeded / failed / cancelled.
- **Active sessions**: how many conversations are live.
- **Execution ranking (last 7 days)**: top 6 agents by task count.

> The small "实时" (live) badge in the top right means the page refreshes on a timer -- no F5 needed.

### 6.2 Quick access

![Quick access](./screenshots/en-US/03_quick_access.png)

Four buckets: projects / agents / sessions / plans.

The whole usage is one sentence: **go to the list page, click the ★ on a row, and it shows up
here.** Each bucket's "go to the … list" link jumps straight there.

### 6.3 Compute hosts

![Compute hosts](./screenshots/en-US/04_hosts.png)

The machine inventory, from the kernel's `agx_host_inventory`.

Each card/row carries: host name, alias, IP, SSH port, OS, slot limit, current occupancy,
online state, last heartbeat, and how many agents live on it.

Toggle between **card view and table view** (top right); click the pencil to edit alias, IP,
SSH port, or slot limit.

### 6.4 Agents

![Agents](./screenshots/en-US/05_agents.png)

Every agent, grouped by project (the shot shows the four deployed system agents under `system`).

Filters: search / in-use / project / host / role / online state. Top right switches between
**dense grid and table**.

Each card carries:

- Name plus an **online** badge (green dot)
- Tags: `原生` (built-in), `已部署` (deployed to disk), and the role (project manager / workshop
  manager / host manager / agent manager / worker…)
- Three counters: **sessions / tasks / executions** (tasks = how many kinds of work it can do;
  executions = how many times it actually ran)
- **Send message** -- dispatch work to it
- ★ favourite, 🕐 history

> Template agents (workers not deployed yet) are hidden by the "in use" filter. To see them all,
> switch that filter to "all".

### 6.5 Sessions

![Sessions](./screenshots/en-US/06_sessions.png)

Agent conversations. The strip on top is live: **running / idle / failed / stuck / unknown**.

Per row: session name, owning agent, run state, **context share** (the most useful column --
past roughly 80% it is time to compact), message count, last activity, session ID.

The "send" action at the end of a row talks directly inside that session.

> Context share is colour-coded: blue while low, red as it fills. **When you see red, clear it**,
> or that agent's next turn may fail outright on context size.

### 6.6 Tasks

![Tasks](./screenshots/en-US/07_tasks.png)

The **task template catalogue** -- a list of what an agent *can* do, not a run history.

Columns: task (description) / `task_name` (stable identifier) / owning agent / category
(`universal` / `general` / `watchdog_alert` / …) / visibility (shared / private) / state
(enabled / disabled) / updated / edit.

Filters: search / category / state / agent.

> Why does the same name repeat? Because templates are **per agent** -- every agent owns one
> `universal` row. That is where the 62 rows in the screenshot come from.
>
> The task text itself ships **with the product** (it comes from `agx_struct.json` and the `tasks/*.md`
> templates, seeded into the database at install time). The screenshot is taken on a Chinese
> deployment, so the descriptions read Chinese while the interface around them is English -- that is
> data, not an untranslated label. A deployment that needs English task text edits those templates.

### 6.7 Executions

![Executions](./screenshots/en-US/08_executions.png)

**Every real run** lands here. This is the first place to look when something goes wrong.

Columns: `run_uuid` / task / **sender → receiver** / session / state / started·duration / actions.

Filters are thorough: run_uuid, state, host, **trigger** (manual / plan / wake-up…), project,
time window, agent.

Click the pencil to expand: full instruction, result, SUMMARY, and the event timeline (when it
was dispatched, when it was woken, when it was acknowledged).

> The two rows above are the demo run from section 7: one `已成功 65s`, one still `运行中`.

### 6.8 Quick commands

![Quick commands](./screenshots/en-US/09_quick_cmds.png)

Frequently used operations as cards -- **fill a few parameters and send**, instead of typing a
long instruction every time.

Each card: name, logical ID, type tag (dispatch / agent / host / network / notice / project),
enabled state, visibility, **executor** (which agent performs it), and parameter count.

Buttons: ▶ run / ✏ edit / 🗑 delete. Top right: "+ new command".

Typical contents: clone an agent, create an agent, deploy an agent, configure an agent, collect
host info, network usage analysis, notice summarisation, create a project, deactivate a project…

### 6.9 Shared skills

![Shared skills](./screenshots/en-US/10_skills.png)

The global skill library. A skill is **a reusable, followable "how to"** (commands, pitfalls,
examples). The split from notices is deliberate:

| | What goes here |
|---|---|
| **Shared skills** | "how to get around this pitfall" -- readable and actionable |
| **Notices** | "what I did today" -- a record that something happened |

Search covers name / description / tags / contributor; filter by category. A fresh install is
empty here; it becomes valuable as the system is used (popular skills rise to the top).

### 6.10 Notices / work logs

![Notices](./screenshots/en-US/11_notices.png)

What agents wrote down: work logs, decisions, pitfalls, handoffs, configuration changes.
Filterable by type, visibility, project, agent, and originating task.

### 6.11 Projects

![Projects](./screenshots/en-US/12_projects.png)

A project is the container for tasks, agents, and compute. Each row: project name, abbreviation,
**members** (a row of tags -- instantly see who is on it), state, created at.

A fresh install has only `system`, holding the 12 system-level agents (`agx_core` /
`agx_agentfather` / `agx_projectmother` / `agx_host_<your host>` / `super_pm` / the worker templates).

### 6.12 Project groups

![Project groups](./screenshots/en-US/13_project_groups.png)

Reusable team templates: pack a set of agents together; bind the group to a new project and the
whole roster is cloned in one go.

Columns: group / description / members / bound projects / state / updated.

Three ship by default: `agx-core-group`, `Python-Vue 开发小队`, `图书创作小队`.

### 6.13 Plans

![Plans](./screenshots/en-US/14_plans.png)

Multi-step plans and their progress. Filters: plan / owner / state / host.

Open one to see each step's state, its predecessor dependency, and whether it runs serially or
in parallel (fan-out / fan-in).

> Empty is normal -- a fresh install has not run a plan yet. `plan_generate_and_deploy` in Quick
> commands generates the first one.

---

### 6.14 Service settings

The **Service settings** button at the top right opens a **window** (it is not in the left rail).
It has two tabs.

**Speech service** -- choose and probe the speech-to-text hosts:

![Service settings · speech](./screenshots/en-US/15_service_asr.png)

- The list comes from `BFF_ASR_HOSTS` in the server-side `.env`. Each row shows: name, WebSocket
  address, note, availability (available / unreachable / untested), and when it was last probed.
- Selecting a host switches **immediately** and probes it once on the spot. The preference is stored in
  `webtools/server/.runtime/service_settings.json`, so it survives a restart.
- The per-row "check" button forces one probe (the cost of a single WebSocket handshake).
- Availability is maintained by a background probe every 8 seconds. So "the service is back" is
  discovered by the system, instead of by a person clicking once more and failing.

**MCP connection** -- the kernel channel: gateway address, tool count, reachability.

> This window answers exactly one question: **the speech service can live on many machines; which one
> do we use?** Changing *what is available* means editing `.env` and restarting the BFF. Changing
> *which one this time* is a click here, effective at once. Details in chapter 8.

---

## 7. One full walkthrough: dispatch a task

This is the most common path. Do it once and the whole system makes sense.

### Step 1 — pick the agent

Go to **Agents** and click **Send message** on the target card (or use the global
"Send message" button at top right).

### Step 2 — choose a template, write the instruction

The dialog wants three things:

| Field | What to put |
|---|---|
| Receiving session | the main session is fine (`agent:<name>:main`) |
| Task template | which kind of work. For one-off jobs choose `universal` |
| Instruction | plain language, what you want it to do |

One rule for instructions: **say what output you want, and what not to do.** For example:

```
Report this machine's CPU core count and total memory in one sentence. Do nothing else.
```

### Step 3 — watch it run

The instant you dispatch, **Executions** shows a row in `运行中` (running):

![Executions](./screenshots/en-US/08_executions.png)

The state changes on its own: `运行中` → `已成功` (succeeded), or `失败` (failed).
The successful row above took 65 seconds.

### Step 4 — read the result

Click the pencil on that row to see the full instruction, the executor's SUMMARY, and the event
timeline.

Example result:

> CPU 4 cores, total memory 8016384 kB (about 7.6 GiB / 8 GB).

### Step 5 — look at the session

Back on **Sessions**, that agent now has a conversation where there was none, holding your two
messages and the **context share**:

![Sessions](./screenshots/en-US/06_sessions.png)

---

## 8. Voice input (speech to text)

### 8.1 Where it is

![Agents](./screenshots/en-US/05_agents.png)

The microphone next to the input box in the send-message dialog. Click once to start recording, click
again to stop; it **stops itself at 60 seconds** (forgetting to stop does not record forever).

Behaviours that are deliberately fixed:

| Behaviour | Why |
|---|---|
| You **cannot send** while recording or transcribing | otherwise half a sentence goes out |
| The transcript is **appended**, never overwriting | you may already have typed something; overwriting is the least acceptable failure |
| Failures distinguish four causes: no permission / no device / service down / timeout | those four need completely different actions from you |
| A waveform runs while recording | the two most common failures are "mic muted by the system" and "forgot to press stop", and both look like "I talked for ages, why no text"; a moving waveform proves sound went in |

### 8.2 The path

```
Browser microphone
   │  POST /v1/audio/transcribe   (Bearer token)
   ▼
BFF  :8090
   │  WS  ws://<speech host>:<port>/ws/audio/audio2text/{client_id}
   ▼
Speech service (ASR)  →  text
```

**Why the browser does not connect to the speech service directly** -- three reasons:

1. The address would leak into the frontend bundle, so moving the service means repackaging; for
   remote customers a hard-coded `127.0.0.1` does not exist at all.
2. It bypasses authentication -- every `/v1/*` requires a Bearer token, a direct connection has no gate.
3. Error attribution scatters: unreachable / timeout / model not loaded would all have to be guessed in
   the browser.

So **the BFF is the only front door**, the speech address lives only in the server-side `.env`, and the
frontend only ever knows `/v1/audio/transcribe`.

### 8.3 Configured in two places (the easiest thing to mix up)

| Where | What it holds | Who changes it | Restart needed |
|---|---|---|---|
| `BFF_ASR_HOSTS` in `webtools/server/.env` | **what is available** (a deployment fact) | ops / whoever deploys | yes: `./start.sh restart` |
| the choice in the Service settings window | **which one this time** (a runtime preference) | any signed-in user, one click | no |

One sentence: **the list is a fact, the choice is a preference.**

How `.env` looks (a JSON array):

```bash
BFF_ASR_HOSTS=[{"id":"asr-a","name":"Local ASR","url":"ws://127.0.0.1:9998/ws/audio/audio2text","note":"this box · CPU"},{"id":"asr-b","name":"GPU box","url":"ws://10.0.0.21:18888/ws/audio/audio2text","note":"LAN · dedicated GPU"}]

# fallback used only when the list above is empty (a single-machine install can set just this)
BFF_ASR_WS_URL=ws://127.0.0.1:9998/ws/audio/audio2text

BFF_ASR_TIMEOUT=30          # a cold start has to load the model (~10s); 30s covers both warm and cold
BFF_ASR_HEALTH_INTERVAL=8   # background probe interval, seconds
```

Three points that matter:

- `url` **must end at `audio2text`**; the `client_id` segment is appended by the BFF on every call.
- `id` is the only key behind "which one was chosen". **Changing an `id` is changing machines** -- the
  previous choice stops matching and falls back to the first host.
- A bad list (invalid JSON / missing `url` / duplicate `id`) **never stops the BFF** -- it degrades to a
  single default host and logs one warning. Speech is an auxiliary feature; a typo there must not take
  the whole service down.

### 8.4 Start a speech service

Two implementations ship in the tree; pick one:

| Implementation | Engine | Footprint | How to start |
|---|---|---|---|
| `tools/audio_stt/audio_service_server.py` | SenseVoiceSmall (funasr) + VAD | light, runs on CPU, model downloads on first use | `python tools/audio_stt/audio_service_server.py --host 0.0.0.0 --port 9998` |
| `tools/audio_stt/audio_service_server_qwen_asr.py` | Qwen3-ASR-1.7B | about 4.7 GB of weights, GPU recommended | `./tools/audio_stt/start_qwen_asr.sh start` |

For the Qwen path, run the **environment self-check** first (it only inspects, it does not start):

```bash
./tools/audio_stt/start_qwen_asr.sh check
```

It tells you item by item what is missing and how to fix it. Four things it checks, and all four are
real pitfalls:

| Check | What happens when it fails |
|---|---|
| the interpreter can `import qwen_asr` | it will not start at all |
| `torch` version (pin 2.7.0) | `torch.cuda.is_available()` is False, it silently falls back to CPU and is tens of times slower |
| `websockets` is installed | uvicorn **silently degrades to plain HTTP** and every WebSocket request 404s -- the hardest one to diagnose |
| the model directory has `config.json` + `*.safetensors` | it will not start (an unfinished download looks identical) |

Once the check passes, `start`. The script also has `stop` / `restart` / `status` / `log` / `run`
(foreground, for pm2 or systemd):

```bash
pm2 start ./tools/audio_stt/start_qwen_asr.sh --name qwen-asr -- run
pm2 save
```

**Ports**: if one BFF must offer both a local and a remote host, the ports have to differ (say 9998
locally, 18888 remotely). With a single host, any port is fine.

### 8.5 Verifying that it really works

Verify all three layers; do not skip one:

**Layer 1 -- the service itself is alive**

```bash
./tools/audio_stt/start_qwen_asr.sh status     # or
curl -s http://127.0.0.1:9998/
```

**Layer 2 -- the BFF can reach it**

The host shows "available" in the Service settings window, or ask the endpoint directly:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8090/v1/settings/asr
```

Look at `hosts[].state` (`up` or `down`); `error` carries the concrete reason (for example
`ConnectionRefusedError`).

**Layer 3 -- it really transcribes**

```bash
curl -s -X POST http://127.0.0.1:8090/v1/audio/transcribe \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: audio/wav" \
     --data-binary @test.wav

→ {"text":"本机CPU4核内存8G。","engine":"sensevoice","host_id":"asr-a","attempts":1,"duration_ms":8256}
```

The response carries `engine` and `host_id` -- **you can see which machine did the work** without
digging through logs. The line above is a real measurement (cold start including model load, 8.2 s;
warm is typically under a second).

### 8.6 Troubleshooting

| Symptom | Real cause | What to do |
|---|---|---|
| The mic button does nothing, or reports no permission | the browser was not granted microphone access | allow it in the site settings on the left of the address bar. **Note: on plain `http://` (anything but localhost) browsers disable the microphone outright** -- you need https or localhost |
| It spins and then times out | the service is up but the model has not finished loading (cold start) | raise `BFF_ASR_TIMEOUT`, or warm it up with one manual transcription |
| "Service unavailable" | the BFF probe fails | check the `url` and the port. **Moving the service and forgetting to update `.env`** is the most common cause |
| Every WS request 404s in the service log | `websockets` is missing, so uvicorn degraded to plain HTTP | install `websockets` and restart the service |
| The transcript is empty | no speech in the audio, or the system muted the mic | watch the waveform while recording |
| `ERROR: ...` arrives and the connection never closes | the service's error frame carries no end marker | that is existing ASR behaviour; the BFF relies on the 30 s timeout -- and go read the service log |

> ⚠️ **The HTTPS rule is the one people miss**: browsers only grant microphone access on `https://` or
> `localhost`. Open the console at `http://192.168.0.x:5190` and the browser disables the microphone --
> that has nothing to do with the BFF or the speech service, so do not debug in the wrong place.

---

## 9. Troubleshooting

### The page opens but every list is empty

99% of the time the BFF cannot read kernel data. Check in this order:

1. **`/readyz`**: `curl -s http://127.0.0.1:8090/readyz`
   A healthy answer looks like:
   ```json
   {"status":"ok","kernel":{"mcp_sse":true,"tool_count":96,"mysql":true,"redis":true}}
   ```
   `mcp_sse: false` → the MCP gateway is unreachable; go to step 2.
2. **MCP gateway**: does `curl -s http://127.0.0.1:31890/agentgeex/mcp` answer? If not,
   `./mcp.sh start`.
3. **`network_id` mismatch**: the classic one. Early BFF builds hard-coded the network as
   `lan_001`; if your `agx_config.json` says anything else, every `WHERE network_id = ?` matches
   nothing -- all lists empty while System status still looks fine (that page uses a different
   query). Fixed to read the config; an old build shows exactly this symptom.

### `/readyz` hangs and the log repeats `mcp session lost ... cannot import name 'streamablehttp_client'`

The `mcp` Python package renamed `streamablehttp_client` to `streamable_http_client` in 1.28,
so the old BFF import fails, MCP never connects, and `/readyz` hangs.

Fixed (both names accepted). If your build still reports it, update the code, or pin `mcp` to 1.27.0.

### A dispatched task stays "running" with an empty `started_at`

It was sent but the other side never started. Read `~/.agx/logs/ws_client.log`:

- `no such table: session_key_contract` → the openclaw state DB schema is off. Run
  `openclaw doctor --fix`, then restart the gateway.
- `会话不存在，自动创建` (session missing, creating) and then nothing → that agent is not
  registered/deployed in openclaw.
- Nothing at all → the daemon is not running. `~/.agx/daemon.sh status`.

### The frontend fails with `nohup: 无法运行命令 'npm'`

No node on PATH in a non-interactive shell. `export PATH=<your node bin>:$PATH` first, then re-run
`./start.sh start`.

### Opening it from another machine: page loads, no data

The frontend hard-codes its API base to `http://127.0.0.1:8090`. When you browse
`http://<server-ip>:5190` from **your own laptop**, the browser tries to reach port 8090 on
*your* machine, which is not there.

Two ways out:
- Start the frontend with an explicit base:
  `VITE_API_URL=http://<server-ip>:8090/v1 npm run dev`
- Or just view it on the server itself (local browser or remote desktop).

---

## 10. Stopping and reinstalling

```bash
cd ~/.agx/webtools
./start.sh stop            # stop BFF + frontend (MCP belongs to PM2; this script leaves it alone)
pm2 stop agx-mcp-gateway   # only when you really mean to stop MCP
```

**Full reinstall** (start from scratch):

```bash
cd <source dir>
python3 install.py --uninstall   # removes agents and clears the database
```

⚠️ **Measured caveat**: today `--uninstall` only removes the agent records from the gateway.
It does **not** delete `~/.openclaw/agents/<id>/`, and it will not proceed to clear the install
directory on its own (it asks for confirmation). Before reinstalling, do one extra step:

```bash
rm -rf ~/.openclaw/agents/agx_*      # leftover directories
```

Otherwise `install.py` will **reuse** the previous agent IDs found in the gateway -- and you will
see an old machine name appear on the new machine.

> One more thing to remember: when **a second instance** lives on the same machine (a different
> `--workdir`), the config file must be passed explicitly or the installer reads the default one:
>
> ```bash
> AGX_CONFIG_PATH=~/.agx_demo/agx_config.json \
> AGX_CREDS_ENV=~/.agx_demo/agx_creds.env \
> python3 install.py --core --workdir ~/.agx_demo
> ```

---

## Appendix: what this UI is built from

| Layer | Technology |
|---|---|
| Frontend | React + Refine + Tailwind + shadcn/ui, built by Vite, bilingual zh/en |
| BFF | FastAPI + uvicorn; JWT (HS256, a dependency-free implementation) |
| Kernel channel | MCP over streamable-http |
| Data | MySQL (source of truth) + Redis (cache) |

Every list on screen maps to one `GET /v1/<resource>` on the BFF. Each request carries the
signed-in user's JWT; the BFF validates it, then fetches from the kernel under a fixed identity
(`agx_core`).

---

*License: Apache-2.0. All screenshots come from a real test install and contain no production host names.*
