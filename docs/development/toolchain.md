# AgentGeeX Toolchain Installation Standard

> Version: v1 (2026-10-27)
> Applies to: onboarding a new machine into AgentGeeX, preparing a runtime for an Agent, migrating a host
> Measured baseline: macOS 14.5 (`test_host_mac` / 10.0.0.11), Ubuntu 20.04 (10.0.0.12), Ubuntu 22.04 (10.0.0.13)
> Language note: prose is in English; commands, paths and identifiers are kept verbatim

---

## 1. What This Document Solves

Getting a machine from zero to "able to run AgentGeeX" means gathering tools that are scattered across a bunch of official install scripts, each with a lot of implicit prerequisites (Python version, PATH, proxy, mirrors). This document covers:

1. **What to install** — split into required / recommended / optional, so that "it runs" and "it runs well" do not get mixed together
2. **How to install it** — idempotent scripts; running them again produces no side effects
3. **How to configure networking** — which traffic should go through the proxy and which must use a domestic mirror (measured: a 50 times difference)
4. **How to verify it** — the expected output of every command; after installing you must go through them one by one
5. **What pitfalls were hit** — all of them were actually run into, not speculated

---

## 2. Layered Checklist

### 2.1 Required (without these AgentGeeX will not run)

| Tool | Purpose | Acceptance criterion |
|---|---|---|
| `curl` | entry point for every install script | whatever ships with the OS |
| `git` | pull repositories, read the commit line | `git --version` |
| `uv` | Python runtime and package management (AgentGeeX uses uv uniformly, never the system pip) | `uv --version` |
| `Python ≥ 3.10` | AgentGeeX kernel, MCP, daemon | `python3 -V`; below 3.10, uv manages it |
| `Node.js LTS` | only needed on hosts that run an OpenClaw gateway | `node -v` (baseline v22.22.3) |
| `jq` | the default tool for Agent scripts to parse JSON | `jq --version` |

### 2.2 Recommended (what an Agent needs to get work done)

| Tool | Command name | Purpose | Install source |
|---|---|---|---|
| HF CLI | `hf` | download models/datasets, `hf jobs`, upload | `https://hf.co/cli/install.sh` |
| Tavily CLI | `tvly` | online search (agents looking things up) | `https://cli.tavily.com/install.sh` |
| MySQL client | `mysql` | query the `agentgeex` database directly | system package / brew |
| Redis CLI | `redis-cli` | inspect queues, cache, watchdog state | system package / brew |

> ⚠️ The Tavily CLI command name is **`tvly`**, not `tavily`. After installing, `command -v tavily` failing is normal.

### 2.3 Optional (as needed)

| Tool | Purpose |
|---|---|
| `docker` | containerized tasks, sandboxed execution |
| `gh` | GitHub CLI (PR/issue operations) |
| `ripgrep` (`rg`) | full-text search in large repos (an order of magnitude faster than grep) |
| `ffmpeg` | audio/video processing tasks |
| `wget` | some install scripts depend on it |

---

## 3. One-Shot Install (idempotent)

```bash
#!/usr/bin/env bash
# AgentGeeX toolchain install — safe to run repeatedly
set -euo pipefail

PROXY="${PROXY:-}"   # e.g. PROXY=http://10.0.0.11:7890 ; empty = direct connection
if [ -n "${PROXY}" ]; then
  export https_proxy="${PROXY}" http_proxy="${PROXY}"
  export HTTPS_PROXY="${PROXY}" HTTP_PROXY="${PROXY}"
fi

say() { printf '\n=== %s ===\n' "$*"; }

# ── 1. uv ────────────────────────────────────────────────
say "uv"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# ── 2. hf CLI ────────────────────────────────────────────
# Two hard prerequisites: (1) must use bash (sh/dash fails with a syntax error) (2) requires Python >= 3.10
say "hf CLI"
need_py() { python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; }
if ! need_py; then
  echo "系统 python3 低于 3.10，改用 uv 托管版本"
  uv python install 3.12
  mkdir -p "$HOME/.pybin"
  ln -sf "$(uv python find 3.12)" "$HOME/.pybin/python3"
  export PATH="$HOME/.pybin:$PATH"
fi
curl -LsSf https://hf.co/cli/install.sh | bash

# ── 3. tvly CLI ──────────────────────────────────────────
say "tvly CLI"
curl -fsSL https://cli.tavily.com/install.sh | bash

# ── 4. Verify ────────────────────────────────────────────
say "verify"
for c in uv hf tvly; do printf '%-6s %s\n' "$c" "$(command -v "$c" || echo MISSING)"; done

# ── 5. Persist PATH (visible to login shells) ────────────
say "PATH"
for f in "$HOME/.profile" "$HOME/.bashrc"; do
  grep -q 'agentgeex local bin' "$f" 2>/dev/null && continue
  cat >> "$f" <<'BLOCK'

# >>> agentgeex local bin >>>
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) PATH="$HOME/.local/bin:$PATH" ;;
esac
export PATH
# <<< agentgeex local bin <<<
BLOCK
done
```

Usage:

```bash
PROXY=http://10.0.0.11:7890 bash install_toolchain.sh
```

> On Linux the `hf` install script **must** be run with `bash`. Using `sh` (which is dash on Ubuntu) fails with
> `install.sh: line 1: syntax error near unexpected token ')'` — this is a real measured failure.

---

## 4. Proxy Strategy (this section matters more than what you install)

### 4.1 Available Proxy

| Item | Value |
|---|---|
| Address | `10.0.0.11:7890` (reachable on both en8 and en0) — the same Mac, `xxyunCore` listening on `*:7890` |
| Protocol | both HTTP and SOCKS5 **work** (`http://` or `socks5h://`) |
| Egress IP | measured `45.59.187.2` / `45.59.186.74` (they rotate, which means a node pool) |
| Reachability | 10.0.0.12 / 10.0.0.13 can both connect directly to that port (same LAN segment) |

### 4.2 Measured Speed (this decides the split strategy)

| Path | Measured speed | Notes |
|---|---|---|
| Large pypi package through the proxy | **23 KB/s** | 4.1MB took 180s |
| Cloudflare 10MB through the proxy | **68 KB/s** | 8.2MB took 120s |
| Direct to the Tsinghua pypi mirror | **1.2 MB/s** | index page in 1.07s |
| pypi simple page through the proxy (small) | 0.5s | small requests are not slow; the bottleneck is throughput |

**Conclusion: do not funnel all traffic into the proxy.** The proxy solves "blocked", not "fast".

### 4.3 Routing Rules

**Must go through the proxy** (no usable domestic source / blocked):

```
github.com  raw.githubusercontent.com  codeload.github.com  objects.githubusercontent.com
hf.co  huggingface.co  cli.tavily.com  astral.sh
google.com  various overseas APIs
```

**Do not go through the proxy; use a domestic mirror directly**:

| Ecosystem | Which mirror | How to configure |
|---|---|---|
| Python packages | `https://pypi.tuna.tsinghua.edu.cn/simple` or `https://mirrors.aliyun.com/pypi/simple/` | `pip config set global.index-url ...` or `UV_DEFAULT_INDEX=...` |
| Node packages | `https://registry.npmmirror.com` | `npm config set registry ...` |
| apt | `https://mirrors.aliyun.com/ubuntu/` | replace `/etc/apt/sources.list` |
| HF models | `https://hf-mirror.com` | `export HF_ENDPOINT=https://hf-mirror.com` |

### 4.4 Proxy Syntax per Tool

```bash
# Generic (curl / wget / most CLIs honour these)
export https_proxy=http://10.0.0.11:7890
export http_proxy=http://10.0.0.11:7890

# git (can also be written globally into config)
git -c http.proxy=http://10.0.0.11:7890 clone ...

# A single curl request
curl -x http://10.0.0.11:7890 https://github.com

# socks5 (some tools only accept this one)
export all_proxy=socks5h://10.0.0.11:7890

# Exclude the LAN and the mirrors (note: not every tool supports CIDR, so domain names are safest)
export no_proxy=localhost,127.0.0.1,10.0.0.11,\
pypi.tuna.tsinghua.edu.cn,mirrors.aliyun.com,registry.npmmirror.com,hf-mirror.com
```

### 4.5 Two Recommended Modes

**Mode A: mirrors first, proxy on demand (recommended)**

Day-to-day package installs use domestic mirrors (50 times faster); only turn the proxy on when touching github / hf.co:

```bash
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
export HF_ENDPOINT=https://hf-mirror.com
npm config set registry https://registry.npmmirror.com
# when needed: export https_proxy=http://10.0.0.11:7890
```

**Mode B: proxy everything (simple but slow)**

Suitable when installing only small things, or when the mirrors are unreachable. Do not use this mode for large packages (torch, model weights).

---

## 5. PATH and Runtime Environment (the section most easily missed in practice)

### 5.1 Symptom

After installing `uv / hf / tvly` they all land in `~/.local/bin`, but:

| Scenario | test_host_mac | 10.0.0.12 | 10.0.0.13 |
|---|---|---|---|
| Interactive login (typing commands after `ssh host`) | ✅ | ✅ (managed block in `.profile`/`.bashrc`) | ✅ (same as left) |
| Non-interactive (`ssh host 'hf --version'`) | ✅ | ❌ **absolute path only** | ✅ but only for those in `/usr/local/bin`, `/usr/bin` |
| systemd user service | — (no user services) | ✅ **since 2026-10-27** (see 5.3) | ✅ but only for binaries in system directories |

Measured conclusion (re-checked 2026-10-27):
- On 10.0.0.12, `~/.local/bin` is added by the `agentgeex local bin` managed block in `.bashrc` and **only takes effect for interactive shells**;
  the PATH of `ssh host 'cmd'` (non-interactive, non-login) is still the minimal system set and **does not contain** `~/.local/bin`.
- 10.0.0.13 is the same in principle; but on 10.0.0.13 "`/usr/local/bin` is in the minimal PATH and comes before `/usr/bin`", so **tools installed into `/usr/local/bin` are visible in every scenario**.

### 5.2 Three Solutions (pick by scenario)

| Scenario | Approach |
|---|---|
| A human using it interactively | Append the guarded block from step 5 of the script above to `~/.profile` + `~/.bashrc` ✅ already done |
| An Agent invoking it via `bash -lc "..."` | The same is enough (a login shell reads `.profile`) |
| Invoking from a systemd user service | ① (recommended, persistent) write `PATH=...` into `~/.config/environment.d/10-agentgeex-path.conf` and apply immediately with `systemctl --user set-environment PATH=...` (see 5.3); ② only for a single unit: `[Service]` + `Environment=PATH=...` in `~/.config/systemd/user/<unit>.service.d/10-path.conf`; ③ install into `/usr/local/bin` when passwordless sudo is available (that is how 10.0.0.13 does it); ④ last resort: write the absolute path `$HOME/.local/bin/hf` in code |

### 5.3 systemd --user PATH: how environment.d actually takes effect (measured 2026-10-27, systemd 245)

Three pitfalls were hit on 10.0.0.12 and all conclusions are reproducible:

1. **Writing `environment.d` alone + `systemctl --user daemon-reexec` ≠ immediate effect.**
   The file was written and both `daemon-reload` / `daemon-reexec` were run, yet `show-environment` and a **newly started transient service** still had the old PATH.
   environment.d is read when the user manager **starts**; on 245 `daemon-reexec` does not regenerate that environment.
2. **`systemctl --user set-environment PATH=...` takes effect immediately** (no service restart needed, no re-login needed).
   Measured: right after setting it, `show-environment` contains `~/.local/bin`, and inside a **newly started** transient service `command -v gh` = `~/.local/bin/gh`.
   Note that this is **runtime state** and is lost on reboot/re-login — so use it together with environment.d (the former is immediate, the latter persistent).
3. **`systemd-run --user --scope` cannot be used to verify**: the process inside the scope is forked by `systemd-run` itself and
   inherits the **calling shell's env**, which produces a false positive of "already in effect". You must use
   `systemd-run --user --unit=<name> --collect ...` (a real service) or read `/proc/<pid>/environ` directly.

Also: **changing PATH does not affect already-running processes.** A running `agx-daemon.py` / `openclaw-gateway` keeps the env it was started with and
must wait for the next start — this is not "the fix did not work".

> One-line criterion: **PATH is the shell's business, not the installer's.** Installing does not mean it can be called; how to bridge that depends on the caller.
> Also do not trust `--scope` when verifying: to check "can a new service see it", make systemd actually start a unit.

---

## 6. Authentication (tokens never go into docs, never into the repo)

```bash
hf auth login          # paste the HF token interactively; or use the HF_TOKEN env var
hf auth whoami         # expected output user=<your account>

tvly init              # configure the Tavily API key interactively
tvly search "test" --max-results 1   # expected: 1 result returned
```

Measured notes:

- `hf` can download public models anonymously too (`hf download hf-internal-testing/tiny-random-gpt2 config.json` works)
- `tvly` **can search without init** — the first call creates an anonymous session (rate-limited); for real use, configure your own key

---

## 7. Acceptance Checklist (run every item after installing)

```bash
# 1. Binaries exist
for c in uv hf tvly git jq; do printf '%-6s %s\n' "$c" "$(command -v $c || echo MISSING)"; done

# 2. Versions
uv --version && hf version && tvly --version

# 3. Network (export the proxy first when it is needed)
export https_proxy=http://10.0.0.11:7890 http_proxy=http://10.0.0.11:7890
curl -s -o /dev/null -w '%{http_code}\n' https://github.com          # expected 200
curl -s https://api.ipify.org; echo                                  # expected: the proxy egress IP

# 4. hf can really download something
cd /tmp && hf download hf-internal-testing/tiny-random-gpt2 config.json --quiet \
  && find ~/.cache/huggingface -name config.json -newermt '-5 minutes' | head -1

# 5. tvly can really search
tvly search "agx" --max-results 1
```

Expected result: everything produces output with no error; `hf download` prints the local cache path; `tvly search` returns entries.

---

## 8. Current State of the Three Machines (measured 2026-10-27)

| Tool | test_host_mac (macOS 14.5) | 10.0.0.12 (Ubuntu 20.04) | 10.0.0.13 (Ubuntu 22.04) |
|---|---|---|---|
| uv | 0.10.12 | 0.11.7 | 0.11.7 |
| Python | 3.13.5 (homebrew) | 3.8.10 system + 3.14.4 (uv-managed) | 3.10.12 |
| Node | v22.22.3 | **v22.22.3** (nvm) / v24.14.1 | v22.22.3 |
| npm | 10.9.8 | **10.9.8** (nvm) | 10.9.8 |
| hf | **2.3.0** | **2.3.0** | **2.3.0** |
| tvly | 0.1.8 | **0.1.8** | **0.1.8** |
| git | 2.39.5 | 2.25.1 | 2.34.1 |
| jq | 1.8.1 | **1.8.2** (static binary) | 1.6 |
| docker | 27.5.1 | 24.0.7 | 28.0.4 |
| mysql | 8.0 (mysql-client@8.0) | 8.0.39 | 8.0.46 |
| redis-cli | 6.2.1 | 7.4.1 | 7.4.1 |
| gh | 2.96.0 (homebrew) | **2.103.3** (`~/.local/bin/gh` = proxy wrapper + `gh-real`) | **2.103.3** (`/usr/local/bin/gh`; the apt 2.4.0 is still in `/usr/bin`, no package-manager action taken) |

Filled-in records from this round (2026-10-27 18:5x–19:1x):

- **10.0.0.12**: installed `uv`, `hf 2.3.0`, `tvly 0.1.8`, `jq 1.8.2`; added the PATH block to `.profile`/`.bashrc`
- **10.0.0.13**: installed `uv`, `hf 2.3.0`, `tvly 0.1.8`; added the PATH block to `.profile`/`.bashrc`
- **local machine**: `hf` 1.24.0 → 2.3.0 (`hf update`); authentication and downloads verified working
- 10.0.0.12 has no passwordless sudo → `jq` was taken from a GitHub Releases static binary into `~/.local/bin`, leaving system packages untouched
- clean state: on all three machines `uv` / `hf` / `tvly` / `jq` are all usable and visible to a login shell
- **10.0.0.12**: installed `gh 2.103.3` → `~/.local/bin/gh-real` (the real binary) + `~/.local/bin/gh` (a wrapper that force-injects `HTTPS_PROXY=http://10.0.0.11:7890`, overridable via `GH_PROXY`); `git config --global http.proxy` set accordingly. Reason: 10.0.0.12 times out connecting directly to `api.github.com` (000/8s), while through the proxy it is 200/0.56s
- **10.0.0.13**: `gh` upgraded on 2026-10-27 to **2.103.3** → `sudo install` into `/usr/local/bin/gh` (**not** apt; `/usr/bin/gh` 2.4.0 is left untouched). `/usr/local/bin` was chosen over `~/.local/bin` because it is in that machine's **minimal PATH** and comes before `/usr/bin`, so daemon / cron / non-login ssh can all see it; `~/.local/bin` is only on PATH for login shells (measured).
- **10.0.0.12**: a non-login invocation of `gh` must use the absolute path `~/.local/bin/gh` (that machine has no passwordless sudo, so it cannot land in `/usr/local/bin`); the PATH for systemd user services has been filled in with `environment.d` + `set-environment` (see 5.3)

> 10.0.0.12's Node comes from nvm (`~/.nvm/versions/node/v22.22.3`, with v24.14.1 also present) and **is not on the login shell's PATH** —
> the openclaw-gateway systemd unit starts it with an **absolute path** `ExecStart=/home/<user>/.nvm/versions/node/v22.22.3/bin/node ...`.
> So `ssh host 'node -v'` failing does not mean it is not installed; check the nvm directory or `systemctl --user cat openclaw-gateway`.

---

## 9. Pitfall Log (all actually hit, not speculated)

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | the `hf` install script errors with `syntax error near unexpected token ')'` | the script uses bash syntax (a `_path` function + arrays); Ubuntu's `sh` is dash | must be `\| bash`, never `\| sh` |
| 2 | `hf` reports `Python 3.10+ is required but was not found` | Ubuntu 20.04's system python3 is 3.8.10 | `uv python install 3.12` + a temporary shim `~/.pybin/python3` |
| 3 | after installing, `ssh host 'hf --version'` says command not found | a non-interactive shell does not read `.profile`/`.bashrc` | see section 5; or write the absolute path |
| 4 | `hf` cannot be found inside a systemd service | the systemd user service PATH does not contain `~/.local/bin` | add `Environment=` to the unit, or use an absolute path |
| 5 | `curl https://pypi.org` times out directly (both 10.0.0.12/10.0.0.13 fail) | overseas source is blocked | use the proxy, or switch to a domestic mirror |
| 6 | downloading a large pypi package through the proxy is only 23 KB/s | poor proxy node throughput | large packages go through the domestic mirror (1.2 MB/s) |
| 7 | `crates.io` returns 403 | that site requires a User-Agent; unrelated to the proxy | add `-H 'User-Agent: ...'` |
| 8 | `github.com` takes 6–7s through the proxy while other sites take 0.5s | the proxy is slow for github (node routing) | acceptable; or use a mirror such as `hub.fastgit` |
| 9 | `hf-mirror.com` is unreachable from 10.0.0.12 (HTTP 000) | network differences on that host | run `curl -I` once before installing models |
| 10 | `command -v tavily` finds nothing | the command name is `tvly` | use `tvly` |
| 11 | remote `printf` of version info errors with `cut: '"': 没有那个文件` | multiple layers of ssh quoting | write the script to a file and use `ssh host 'bash -s' < file` |
| 12 | on 10.0.0.12 `command -v node` is empty, leading to a false "Node not installed" | Node comes from nvm and is not on the login PATH; the gateway unit uses an absolute path | check `~/.nvm/versions/node/*` and `systemctl --user cat openclaw-gateway` |
| 13 | after writing `environment.d` + `daemon-reexec`, `show-environment` still has the old PATH | on systemd 245 environment.d is read only when the user manager **starts**; reexec does not regenerate it | add `systemctl --user set-environment PATH=...` for immediate effect; leave environment.d for the next start |
| 14 | verifying PATH with `systemd-run --user --scope env` says "in effect", yet a real service still cannot find it | a `--scope` process is forked by systemd-run itself and inherits the **calling shell's** env | use `systemd-run --user --unit=<name> --collect` (a real service) or read `/proc/<pid>/environ` |
| 15 | after installing `/usr/local/bin/gh`, `dpkg -S gh` still points at the 2.4.0 apt package, suggesting the upgrade did not happen | dpkg only knows its own packages and ignores `/usr/local/bin` | trust `command -v gh` + `gh --version`; the two layers coexisting is expected |
| 16 | on 10.0.0.13 a GitHub Release through the proxy is only ~12 KB/s vs ~60 KB/s direct; a 15MB package never finishes | poor throughput from that machine to github/proxy nodes | download first on a machine with better throughput (locally through 127.0.0.1:7890 ≈ 83 KB/s) and then `scp` over the LAN |

---

## 10. Follow-Ups

1. To let an Agent call `hf`/`tvly` directly (through a systemd service), the unit needs a PATH per section 5 — not done yet
2. `10.0.0.12`'s Node is not on the login PATH (nvm installed under `~/.nvm/versions/node/v22.22.3`). The gateway already starts with an absolute path, so runtime is unaffected; if you want interactive `node` too, add an nvm block to `.profile` per section 5
3. `hf` is now unified at 2.3.0 on all three machines; `10.0.0.12` additionally holds Node v24.14.1 — switch versions with nvm, do not change the absolute path in the gateway unit
