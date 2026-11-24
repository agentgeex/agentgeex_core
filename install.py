#!/usr/bin/env python3
"""
install.py — the AgentGeeX cross-platform installer

A single file replacing agx.sh and the old shell installer.
Supports macOS / Linux / Windows.

Usage:
    python install.py                  # full install (interactive)
    python install.py --host-only      # agx_host only (worker node)
    python install.py --default        # non-interactive, all defaults
    python install.py --dry-run        # preview without executing
    python install.py --workdir DIR    # custom working directory

Windows:
    agx.bat                        # equivalent to python install.py --default
"""

import argparse
import asyncio
import collections
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pymysql
import redis as redis_mod

# ═════════════════════════════════════════════════════════════
# identity rendering (2026-09-28)
# -----------------------------------------------------------------
# same rules as agx_mcp/mcp_server.py: new templates go through {{AGENT_*}} placeholder substitution,
# old templates are conservatively rewritten as "first-line title + first name line", so a template identity is never stored verbatim.
# ═════════════════════════════════════════════════════════════
_AGENT_MD_PLACEHOLDER_KEYS = (
    "AGENT_ID",
    "AGENT_NAME",
    "AGENT_ROLE",
    "AGENT_MODEL",
    "HOST_ID",
    "PROJECT_ID",
)
_AGENTS_TITLE_RE = re.compile(r"^(#\s*AGENTS\.md\s*[—\-–]\s*).*$")
_AGENTS_NAME_LINE_RE = re.compile(
    r"^(\s*-\s*\*\*\s*名称\s*[:：]\s*\*\*\s*|\s*-\s*\*\*\s*名称\s*[:：]\s*).*$"
)


def _render_agent_md_for_identity(text: str, mapping: dict) -> str:
    """Render a template *_md into the identity of a new instance (idempotent). Same rules as the function of that name in agx_mcp."""
    if not text:
        return text
    out = text
    if "{{" in out:
        for k in _AGENT_MD_PLACEHOLDER_KEYS:
            out = out.replace("{{" + k + "}}", str(mapping.get(k, "")))
    if not out:
        return out
    name = str(mapping.get("AGENT_NAME", ""))
    aid = str(mapping.get("AGENT_ID", ""))
    lines = out.split("\n")
    if lines and _AGENTS_TITLE_RE.match(lines[0]):
        lines[0] = _AGENTS_TITLE_RE.sub(
            lambda m: m.group(1) + f"{name} 工作空间", lines[0], count=1
        )
    for i, ln in enumerate(lines[:25]):
        mm = _AGENTS_NAME_LINE_RE.match(ln)
        if mm:
            lines[i] = mm.group(1) + f"{name} ({aid})**"
            break
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# template md loading (2026-10-25 DH-068)
# -----------------------------------------------------------------
# root cause: the old implementation located the template directory with `labs_fs = agent_labs or aid`, but a per_host_unique
# agent reaching this point already had aid rewritten to [the instance id] (agx_host_srv_a) ->
# agents/agx_host_srv_a does not exist -> _load_md swallowed the exception and returned "" ->
# the five fields AGENTS/SOUL/IDENTITY/USER/TOOLS were silently written empty, and a reinstall did not heal it either.
# Here "how to compute the directory" and "how to read the files" collapse into a single point, and failures become visible.
# ═══════════════════════════════════════════════════════════════

AGENT_MD_FILES = (
    ("agents_md", "AGENTS.md"),
    ("soul_md", "SOUL.md"),
    ("identity_md", "IDENTITY.md"),
    ("user_md", "USER.md"),
    ("tools_md", "TOOLS.md"),
)


def _read_md_with_status(file_path):
    """Read a template md and return (text, status); status is one of {ok, empty, missing}.

    * DH-068: the earlier _load_md collapsed both "file missing" and "file empty" into "",
      so a hard error such as "the template directory was computed wrongly" could not be
      distinguished from "the template really is empty". This function keeps the original
      text (no strip) and reports only the status separately to the caller.
    """
    p = Path(file_path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", "missing"
    except Exception as e:  # permission/encoding errors are all treated as missing, but no longer silently
        log(f"  [WARN] 读取模板失败 {p}: {e}", "WARN")
        return "", "missing"
    return (text, "ok") if text.strip() else (text, "empty")


def resolve_agent_template_dir(install_src, agent_def) -> Path:
    """Resolve the template directory of an agent (agents/<name>).

    * DH-068 root fix: the directory name comes from struct's agent_labs (the template
      directory name); when empty it falls back to struct's agent_id (the template base
      name = directory name). **Never use the instance id** — a per_host_unique instance id
      carries a host suffix (agx_host_srv_a) and the matching template directory does not exist.
    """
    labs_fs = agent_def.get("agent_labs") or agent_def["agent_id"]
    return Path(install_src) / "agents" / labs_fs


def load_agent_md_bundle(install_src, agent_def, agent_id, agent_name, host_id):
    """Load and render the 5 md fields; returns (fields: dict, template_dir: Path).

    The three cases "directory absent / file missing / file empty" each log a WARN — so the
    install log shows at a glance whether the directory was computed wrongly or the template
    itself is at fault, instead of silently producing an empty md.
    """
    labs_fs = agent_def.get("agent_labs") or agent_def["agent_id"]
    tpl_dir = resolve_agent_template_dir(install_src, agent_def)
    if not tpl_dir.is_dir():
        log(
            f"  [WARN] 模板目录不存在: {tpl_dir} | agent={agent_name}({agent_id}) | "
            f"labs_fs={labs_fs} —— 5 个 md 将为空",
            "WARN",
        )
    id_map = {
        "AGENT_ID": agent_id,
        "AGENT_NAME": agent_name,
        "AGENT_ROLE": agent_def.get("role", "worker"),
        "AGENT_MODEL": agent_def.get("model", "deepseek-chat"),
        "HOST_ID": host_id or "",
        "PROJECT_ID": "system_project",
    }
    fields = {}
    for key, fname in AGENT_MD_FILES:
        raw, status = _read_md_with_status(tpl_dir / fname)
        if status == "missing":
            log(f"  [WARN] 模板文件缺失: {tpl_dir / fname} | agent={agent_name}({agent_id})", "WARN")
        elif status == "empty":
            log(f"  [WARN] 模板文件为空: {tpl_dir / fname} | agent={agent_name}({agent_id})", "WARN")
        fields[key] = _render_agent_md_for_identity(raw, id_map)
    return fields, tpl_dir


# ═══════════════════════════════════════════════════════════════
# constants
# ═══════════════════════════════════════════════════════════════

# forbid subprocess imports (avoids a crash from duplicate macOS framework initialization)
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

# * Lao Feng, 2026-11-11: the IDs and names of the 4 built-in platform-management Agents all move to the agx_ prefix.
SYS_CORE = "agx_core"
SYS_HOST = "agx_host"
SYS_AGENTFATHER = "agx_agentfather"
SYS_PROJECTMOTHER = "agx_projectmother"

SYSTEM_AGENTS = [SYS_CORE, SYS_HOST, SYS_AGENTFATHER, SYS_PROJECTMOTHER]

# the host-type Agent name family — the single criterion for "is this a host agent".
# literals are no longer compared all over the file.
HOST_AGENT_NAMES = (SYS_HOST,)

# ═══════════════════════════════════════════════════════════════
# * 2026-10-25 directory structure change (Lao Feng's call)
# ═══════════════════════════════════════════════════════════════
#   ~/.agx/agx_agents/{agent_id}/          <- every agent, flat
#   ~/.agx/agx_agents/host/agx_host_{host}/
#
# * Why host-type agents get their own host/ subdirectory:
#   agx_core / agx_agentfather / agx_projectmother and the production agents are all
#   [globally unique]; only agx_host is [one per host].
#   Filing host-type agents under host/ keeps them from competing for the same namespace as unique agents.
#
# * Why the root is not layered by host (deliberate, not laziness):
#   in the repo every agent sits in [one flat directory] -> this makes agent [drift] possible later.
#   Moving an agent to another host only changes host_id in the DB; the directory stays put. Layering by host
#   would make drift equal to moving directories, breaking the version history.
#
# * The install root is fixed at ~/.agx; the agent directory name is fixed at agx_agents; no legacy-directory probing.

DEFAULT_WORKDIR = ".agx"
AGENTS_DIR = "agx_agents"
HOST_SUBDIR = "host"


def resolve_default_workdir() -> Path:
    """Resolve the default install directory: always ~/.agx."""
    return Path.home() / DEFAULT_WORKDIR


def resolve_agents_dir_name(workdir) -> str:
    """Resolve the agent directory name: always agx_agents."""
    return AGENTS_DIR


def agent_home_rel_dir(agent_name: str, unique_id: str = "", agents_dir_name: str = "") -> str:
    """Return the agent directory's sub-path [relative to the agents root].

    ordinary agent  -> "{agent_id}"
    per_host_unique -> "host/{agent_id}"

    * Why host-type agents go into a host/ subdirectory: there is one per host, so they do not
      share the namespace of globally unique agents; storing them apart avoids confusion.
    """
    aid = unique_id or agent_name
    if agent_name in HOST_AGENT_NAMES:
        return f"{HOST_SUBDIR}/{aid}"
    return aid


# id prefix families of per_host_unique agents (template agent_name -> id prefixes that template has used)
_PER_HOST_ID_FAMILIES = {
    SYS_HOST: ("agx_host",),
}


def _sanitize_host_suffix(host_id: str) -> str:
    """Normalize a host_id into a form usable as a directory / id suffix.

    * Lao Feng, 2026-10-25: per_host_unique agents no longer use random hex as the suffix but
      [the host name] — e.g. agx_host_srv_a / agx_host_srv_b.
      Reason: the id is meant to be read by humans, and random hex does not show which machine
      it is; also, agent drift requires identifying the machine by name.

    Rules: lowercase, anything not [a-z0-9_] becomes an underscore, trim leading/trailing underscores.
    """
    s = re.sub(r"[^a-z0-9_]+", "_", str(host_id or "").strip().lower())
    return s.strip("_") or "host"


# exclusion list as defined by agx.sh
COPY_EXCLUDES = [
    ".venv",
    ".git",
    "uv.lock",
    "logs",
    "agents",
    "test",
    "TODO.md",
    "README.md",
    ".gitignore",
    "agx.sh",
    "agx.bat",
    "agx_host.bat",
    "install_core.sh",
    "install_host.sh",
    "install_wake.sh",
    "wake_core.sh",
    "wake_project.sh",
]

# files cleaned up after installation
CLEANUP_ITEMS = [
    "agx.sql",
    "agx_struct.json",
    "agx_task_base.sql",
    "bak",
    "env.sh.example",
    "env.sh",
    "env.bat",
    "install_core.sh",
    "install_host.sh",
    "webtools",
    "agx.bat",
    "agx_host.bat",
    "install.py",
]

# dependency files to ship into an Agent workspace (relative to workdir)
# * added 2026-10-24, docs/internal/watchdog-openclaw.md:
#   on a watchdog timeout alert, _capture_diag_info in agx_watchdog.py writes
#   template_file="docs/internal/watchdog-openclaw.md", and the Agent opens it with the read tool using a
#   path [relative to the workspace]. Not shipped -> the alert points at an empty path -> silent degradation.
#
# * Lao Feng, 2026-11-02 (TD-E): these items are [copied] into the agent workspace, no more symlinks.
#   The old implementation created absolute-path symlinks (~/... baked into the link),
#   which all break on a different machine or on agent drift, so real file copies are used: redundancy for scalability.
#
# Why .venv is excluded: it is a 398MB / 10,289-file platform-native artifact —
#   bin/python points at cpython-3.12-macos-aarch64-none, entry-script shebangs are absolute paths, and
#   108 .so files under site-packages carry darwin markers -> copying it to Linux must break, and the size alone rules it out.
#   Each host already builds its own native copy in {workdir}/.venv when running install; an agent needing
#   python goes straight to {workdir}/.venv/bin/python. pyproject.toml + uv.lock ship with the copy, so
#   missing dependencies can be rebuilt locally with `uv sync`.
SYMLINK_ITEMS = [
    "docs/internal/ws-direct-execution.md",
    "docs/internal/subagent-contract.md",
    "docs/internal/project-management.md",
    "docs/internal/watchdog-openclaw.md",
    "env.sh.example",
    "pyproject.toml",
    "skills",
    "uv.lock",
]

# Agent config files
AGENT_CONFIG_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "TOOLS.md",
]

# extra Agent workspace files
AGENT_EXTRA_FILES = [
    "agx.env",
    "HEARTBEAT.md",
    "NOTEBOOK.md",
    "MEMORY.md",
    "SUMMARY.md",  # an empty file is enough; the Agent maintains it after startup
]


# ═══════════════════════════════════════════════════════════════
# utility functions
# ═══════════════════════════════════════════════════════════════


def log(msg, level="INFO"):
    prefix = {"INFO": "", "WARN": "", "ERROR": "", "STEP": "│"}
    print(f"[{level}] {prefix.get(level, '')} {msg}")


def log_step(title):
    bar = "═" * 50
    print(f"\n{bar}\n  {title}\n{bar}")


def banner():
    """Print a separator line"""
    print("=" * 55)


def confirm(prompt, default="y"):
    """Interactive confirmation (returns default in non-interactive mode)"""
    if not sys.stdin.isatty():
        return default == "y"
    hint = "Y/n" if default == "y" else "y/N"
    try:
        ans = input(f"{prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default == "y"
    if not ans:
        return default == "y"
    return ans.startswith("y")


def read_input(prompt, default=""):
    """Interactive input (returns default in non-interactive mode)"""
    if not sys.stdin.isatty():
        return default
    try:
        val = input(f"{prompt} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return val if val else default


def which(cmd):
    """Cross-platform wrapper around shutil.which"""
    return shutil.which(cmd)


def os_platform():
    """Return the OS identifier"""
    s = platform.system()
    if s == "Darwin":
        return "macOS"
    elif s == "Windows":
        return "Windows"
    return s


IS_WINDOWS = platform.system() == "Windows"


def py_bin():
    """The Python interpreter name for this platform (Windows: python, Unix: python3)"""
    return "python" if IS_WINDOWS else "python3"


def run(cmd, cwd=None, env=None, check=True, capture=True, timeout=None):
    """Run a command and return (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        if check and r.returncode != 0 and r.stderr:
            log(r.stderr.strip(), "WARN")
        return r.returncode, r.stdout.strip() if r.stdout else "", r.stderr.strip() if r.stderr else ""
    except Exception as e:
        return 1, "", str(e)


def _get_local_ip():
    """Get this machine's LAN IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def _platform_label():
    """OS identifier string, e.g. 'Darwin arm64'."""
    return f"{platform.system()} {platform.machine()}"


_DBConfig = collections.namedtuple("_DBConfig", ["host", "port", "user", "password", "db_name"])


def _db_config(env_cfg):
    """Extract the database connection parameters from env_cfg."""
    return _DBConfig(
        host=env_cfg.get("AGX_DB_HOST", "127.0.0.1"),
        port=int(env_cfg.get("AGX_DB_PORT", 3306)),
        user=env_cfg["AGX_DB_USER"],
        password=env_cfg["AGX_DB_PASS"],
        db_name=env_cfg.get("AGX_DB_NAME", "agentgeex"),
    )


# ═══════════════════════════════════════════════════════════════
# Phase 2: dependency detection
# ═══════════════════════════════════════════════════════════════


def check_deps(mode="core"):
    """Check whether uv / node / npm are installed.

    * 2026-10-25: --host mode starts no daemon and runs no OpenClaw; measured, only uv is
      needed. A missing node/npm degrades to a WARN (no longer blocking), so an unloaded nvm
      PATH does not kill the install. Also, uv gets a PATH fallback probe (~/.local/bin,
      ~/.cargo/bin), which is common in nvm environments.
    """
    log_step("Phase 2: 依赖检测")

    # uv fallback probe (nvm / non-interactive shells often lack ~/.local/bin on PATH)
    if not which("uv"):
        for cand in ("~/.local/bin/uv", "~/.cargo/bin/uv", "/usr/local/bin/uv", "/opt/homebrew/bin/uv"):
            cp = Path(cand).expanduser()
            if cp.exists() and os.access(cp, os.X_OK):
                os.environ["PATH"] = f"{cp.parent}:{os.environ.get('PATH', '')}"
                break

    required = {"uv": "uv"}
    optional = {"node": "node", "npm": "npm"}

    missing = 0
    for name, cmd in required.items():
        path = which(cmd)
        if path:
            log(f"依赖已安装: {name} ({path})")
        else:
            log(f"依赖缺失: {name}", "ERROR")
            missing += 1

    for name, cmd in optional.items():
        path = which(cmd)
        if path:
            log(f"依赖已安装: {name} ({path})")
        elif mode == "host":
            log(f"依赖缺失: {name}（--host 模式不需要，跳过）", "WARN")
        else:
            log(f"依赖缺失: {name}", "ERROR")
            missing += 1

    if missing:
        sys_name = os_platform()
        install_hints = {
            "macOS": "brew install uv node",
            "Linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "Linux-deps": "apt install nodejs npm (如有需要)",
            "Windows": "winget install --id=astral-sh.uv && winget install OpenJS.NodeJS",
        }
        log(f"请先安装缺失依赖: {install_hints.get(sys_name, '请参考各平台安装指引')}", "ERROR")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# Phase 3: create the working directory + copy files
# ═══════════════════════════════════════════════════════════════


def setup_workdir(install_src, workdir, overwrite=False, copy_files=False, dry_run=False):
    """Create the working directory, copy project files, run uv sync

    * Fixed 2026-11-23 (exposed by the clean-machine install drill):
      When the working directory already existed and was non-empty and overwrite was not
      requested, the old logic returned True immediately and skipped every file copy. But
      install doc Step 4.2 explicitly tells the user to `mkdir -p ~/.agx` first and then write
      agx_config.json into it — meaning **following the doc always hits this branch**, so
      agx.sql / agx_struct.json / install.py never reached the working directory, Phase 6.5
      reported "agx_struct.json missing, skipping struct sync", and the install produced an
      empty database.

      The semantics are now:
        overwrite=True        -> wipe and reinstall (unchanged)
        directory exists, non-empty -> **fill in missing files, keep existing ones** (no overwrite)
        directory absent/empty      -> copy normally
    """
    # note: this function no longer prints the Phase header (the main function already did)

    install_src = Path(install_src).resolve()
    workdir = Path(workdir).resolve()

    preserve = False  # True = keep every existing file, only fill in the missing ones
    if workdir.exists() and list(workdir.iterdir()):
        if overwrite:
            log(f"覆盖安装: 清空 {workdir}")
            if not dry_run:
                shutil.rmtree(workdir)
                workdir.mkdir(parents=True)
        elif not copy_files:
            # * no early return any more: the directory may hold a hand-written agx_config.json/creds,
            #   but project files (agx.sql/agx_struct.json) still must be filled in.
            preserve = True
            log("目录已存在：保留现有文件，只补齐缺失的项目文件")
    else:
        if not workdir.exists():
            log(f"创建目录: {workdir}")
        if not dry_run:
            workdir.mkdir(parents=True)

    if dry_run:
        return True

    # copy files (excluding the listed items)
    log("拷贝项目文件...")
    skipped = 0
    for item in install_src.iterdir():
        name = item.name
        if name in COPY_EXCLUDES:
            continue
        dst = workdir / name
        try:
            if preserve and dst.exists():
                skipped += 1
                continue
            if item.is_dir():
                if not dst.exists():
                    shutil.copytree(item, dst, symlinks=True)
            else:
                shutil.copy2(item, dst)
        except Exception as e:
            log(f"拷贝失败 {name}: {e}", "WARN")
    if skipped:
        log(f"保留已有 {skipped} 项，未覆盖")

    # uv sync builds the Python environment
    log("构建 Python 运行环境 (uv sync)...")
    rc, stdout, stderr = run("uv sync", cwd=str(workdir), check=False)
    if rc == 0:
        log("Python 环境构建完成")
    else:
        log(f"uv sync 失败: {stderr}", "WARN")
        log(f"可稍后手动执行: cd {workdir} && uv sync")

    # create the log directory
    (workdir / "logs").mkdir(exist_ok=True)
    log(f"已创建日志目录: {workdir / 'logs'}")

    return True


# ═══════════════════════════════════════════════════════════════
# Phase 4: Agent registration (WebSocket RPC)
# ═══════════════════════════════════════════════════════════════


async def _ws_connect(ws_url, token):
    """Open a WebSocket connection and complete the handshake; returns the ws object."""
    import websockets as _ws  # noqa: F811 — provided by the uv-managed venv

    ws = await _ws.connect(ws_url, ping_interval=30, ping_timeout=10, proxy=None)
    await ws.recv()  # hello
    await ws.send(
        json.dumps(
            {
                "type": "req",
                "id": "conn",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 4,
                    "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write", "operator.admin"],
                    "caps": [],
                    "commands": [],
                    "permissions": {},
                    "auth": {"token": token},
                    "locale": "zh-CN",
                    "userAgent": "agx-installer",
                },
            }
        )
    )
    hello = await ws.recv()
    hdata = json.loads(hello)
    if not (hdata.get("ok") and hdata.get("payload", {}).get("type") == "hello-ok"):
        log(f"WS 握手失败: {json.dumps(hdata, ensure_ascii=False)}", "WARN")
        await ws.close()
        return None
    return ws


async def _recv_response(ws, timeout=10):
    """Read one [response], skipping events the gateway pushes on its own.

    * Fixed 2026-11-23 (measured on a clean machine): the gateway pushes events between
      responses (chat.metadata.changed / sessions.changed / cron ...). Every call site used to
      "send a req -> recv once -> parse it as the response". As soon as an event slipped in, the
      event was parsed as a response: `resp.get("ok")` was empty -> judged "registration
      failed", and measured, only 1 of the 4 system Agents got registered while the install log
      showed no error at all.

    Rule: type == "res" -> a response; type == "event" -> an event, discard and keep reading.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"等待网关响应超时（{timeout}s）")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if msg.get("type") == "event":
            continue
        return msg


async def _get_existing_agents(ws):
    """Fetch the list of already-registered agents over the WebSocket."""
    await ws.send(json.dumps({"type": "req", "id": "1", "method": "agents.list", "params": {}}))
    agents_resp = await _recv_response(ws, timeout=10)
    return {a.get("agentId", a.get("id", "")) for a in agents_resp.get("payload", {}).get("agents", [])}


async def _register_agents_via_ws(workdir, mode, env_cfg):
    """Register the system Agents with the Gateway over WebSocket RPC (reconnecting automatically when the Gateway restarts)."""

    ws_url = env_cfg.get("AGX_GATEWAY_WS_URL", "ws://127.0.0.1:18789")
    token = env_cfg.get("AGX_GATEWAY_TOKEN", "")
    if not token:
        log(
            "AGX_GATEWAY_TOKEN 未设置 —— 跳过 WS Agent 注册。"
            "取值：openclaw gateway auth-token --show，填进 ~/.agx/agx_config.json 的 gateways[0].token",
            "WARN",
        )
        return

    per_host_unique_ids = env_cfg.get("_per_host_unique_ids", {})
    _agents_dir = resolve_agents_dir_name(workdir)
    MAX_RETRIES = 3
    ws = await _ws_connect(ws_url, token)
    if ws is None:
        return

    try:
        existing = await _get_existing_agents(ws)

        # check whether the Gateway already has an old record of the per_host_unique agent (reinstall: reuse the existing id)
        # * 2026-11-11: match by [id prefix family + host suffix], no longer only {agent_name}_.
        #   Otherwise, once agx_host carries a host suffix, a reinstall cannot recognize the existing instance ->
        #   it creates another one in the Gateway, fighting with the old id reused on the DB side.
        _hsuf = _sanitize_host_suffix(env_cfg.get("AGX_HOST_ID", ""))
        for agent_name, generated_id in list(per_host_unique_ids.items()):
            _fam = _PER_HOST_ID_FAMILIES.get(agent_name) or (agent_name,)
            _want = [f"{p}_{_hsuf}" for p in _fam]
            _hit = next((c for c in _want if c in existing), None)
            if _hit is None:
                # other suffixes in the family (from the early random-hex era) are reused too, avoiding duplicates on reinstall
                _hit = next((a for a in existing if any(a.startswith(p + "_") for p in _fam)), None)
            if _hit:
                per_host_unique_ids[agent_name] = _hit
                env_cfg["_per_host_unique_ids"][agent_name] = _hit
                log(f"  发现已有 {agent_name}: {_hit}，复用")
            elif agent_name in existing:
                # bare name (no suffix) means an old-version leftover; use the newly generated unique id
                per_host_unique_ids[agent_name] = generated_id
                env_cfg["_per_host_unique_ids"][agent_name] = generated_id
                log(f"  {agent_name} Gateway 中有旧裸名记录，使用新 ID: {generated_id}")
        # legacy reference compatibility
        if per_host_unique_ids:
            first = next(iter(per_host_unique_ids.values()))
            env_cfg["_host_agent_id"] = first
            env_cfg["_host_agent_id_tentative"] = first

        all_agents = [
            (
                SYS_CORE,
                SYS_CORE,
                "调度核心",
                f"{workdir}/{_agents_dir}/{SYS_CORE}/workspace",
            ),
            (
                SYS_AGENTFATHER,
                SYS_AGENTFATHER,
                "Agent工厂",
                f"{workdir}/{_agents_dir}/{SYS_AGENTFATHER}/workspace",
            ),
            (
                SYS_PROJECTMOTHER,
                SYS_PROJECTMOTHER,
                "项目之母",
                f"{workdir}/{_agents_dir}/{SYS_PROJECTMOTHER}/workspace",
            ),
        ]
        for agent_name, unique_id in per_host_unique_ids.items():
            all_agents.append(
                (
                    unique_id,
                    agent_name,
                    f"{agent_name}(主机唯一)",
                    f"{workdir}/{_agents_dir}/{agent_home_rel_dir(agent_name, unique_id, _agents_dir)}/workspace",
                )
            )
        # host type: with per_host_unique=true the loop above handles it; only when false does it fall through to here
        # * fixed 2026-10-25: this used to be a hard-coded bare path bypassing agent_home_rel_dir() ->
        #   under the new structure it landed on agx_agents/agx_host/ instead of agx_agents/host/agx_host/.
        #   Rule: host-type agents always go under host/, and path joining must go through the single point agent_home_rel_dir().
        if not any(_k in per_host_unique_ids for _k in HOST_AGENT_NAMES):
            all_agents.append(
                (
                    SYS_HOST,
                    SYS_HOST,
                    "主机管理",
                    f"{workdir}/{_agents_dir}/{agent_home_rel_dir(SYS_HOST, SYS_HOST, _agents_dir)}/workspace",
                )
            )

        if mode == "host":
            agents_to_create = [a for a in all_agents if a[1] in per_host_unique_ids or a[1] in HOST_AGENT_NAMES]
        else:
            agents_to_create = all_agents

        rid = 10
        for agent_id, name, _desc, ws_path in agents_to_create:
            # * fixed 2026-11-23 (exposed by the 138 clean-machine acceptance): os.makedirs used to sit after
            #   the "does not exist" branch, so when the agent was already registered with the Gateway the code just
            #   continue'd -> the workspace directory was skipped for good. Gateway registration (agents.create) and the
            #   on-disk directory are two independent facts: a deleted directory, or an install that failed halfway, leaves
            os.makedirs(ws_path, exist_ok=True)

            # ── check whether it already exists ──
            if agent_id in existing:
                log(f"  {name} ({agent_id}): Gateway 已存在，跳过注册（workspace 已确保）")
                continue

            # ── send the create request (reconnecting when the Gateway restarts) ──
            # the name argument becomes the Gateway's agentId, so the uniquified agent_id must be used
            created = False
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await ws.send(
                        json.dumps(
                            {
                                "type": "req",
                                "id": str(rid),
                                "method": "agents.create",
                                "params": {"name": agent_id, "workspace": ws_path},
                            }
                        )
                    )
                    resp = await _recv_response(ws, timeout=10)
                    if resp.get("ok") or resp.get("payload", {}).get("ok"):
                        log(f"  {name}: 注册成功")
                        created = True
                    else:
                        err = (
                            resp.get("error")
                            or resp.get("payload", {}).get("error")
                            or json.dumps(resp, ensure_ascii=False)[:200]
                        )
                        log(f"  {name}: 注册失败 - {err}")
                        created = True  # an explicit error reason; do not retry
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        log(f"  Gateway 连接中断 (attempt {attempt}/{MAX_RETRIES})，等待重连...")
                        with contextlib.suppress(Exception):
                            await ws.close()
                        await asyncio.sleep(3)
                        ws = await _ws_connect(ws_url, token)
                        if ws is None:
                            log("  Gateway 重连失败", "WARN")
                            break
                        # refresh the existing list to avoid registering twice
                        existing = await _get_existing_agents(ws)
                        if agent_id in existing:
                            log(f"  {name}: 已注册（上次请求成功）")
                            created = True
                            break
                    else:
                        log(f"  {name}: 注册失败（重试{MAX_RETRIES}次后） - {e}", "WARN")

            if not created:
                log(f"  {name}: 跳过", "WARN")
            rid += 1
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


def register_agents(workdir, mode, env_cfg, dry_run=False):
    """Register the system Agents over WebSocket RPC (runs in-process)"""
    log_step("Phase 4: 注册 Agent")

    if dry_run:
        agents = SYSTEM_AGENTS if mode == "full" else [SYS_HOST]
        for a in agents:
            log(f"[DRY RUN] agents.create {a}")
        return

    try:
        import websockets  # noqa: F401
    except ImportError:
        log("websockets 未安装，跳过 WS Agent 注册", "WARN")
        return

    # clear the system proxy (a local Gateway connection needs none)
    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            del os.environ[k]

    # * fixed 2026-11-23 (measured on a clean machine): this used straight asyncio.run, so a gateway that
    #   could not be reached raised a bare ConnectionRefusedError traceback and the whole install aborted — Phases 5..8 never ran.
    #   Yet "OpenClaw installed but not initialized" is exactly the normal state of a new machine (the customer installed
    #   the npm package per the doc and ~/.openclaw does not exist yet). That is **an unmet prerequisite**, not an
    #   installer defect, and it must never be presented as an unreadable stack trace.
    #   Now: explain how to fix it and **keep running the remaining Phases** (whatever is already installed is reused).
    try:
        asyncio.run(_register_agents_via_ws(workdir, mode, env_cfg))
    except Exception as exc:  # noqa: BLE001 - what is wanted here is "do not abort the install", not error classification
        _ws_url = env_cfg.get("AGX_GATEWAY_WS_URL", "ws://127.0.0.1:18789")
        _oc_home = Path.home() / ".openclaw"
        log(f"连不上 OpenClaw 网关 {_ws_url}：{type(exc).__name__}: {exc}", "ERROR")
        if not _oc_home.exists():
            log(f"且 {_oc_home} 不存在 —— 说明 OpenClaw 从未初始化过。", "ERROR")
        log("这通常是前置条件没做完，按顺序执行后再重跑安装：", "ERROR")
        log("  1) openclaw setup --baseline                # 首次运行，生成 ~/.openclaw", "ERROR")
        log("  2) openclaw gateway install                 # 装成常驻服务（或前台跑 openclaw gateway）", "ERROR")
        log("  3) openclaw gateway auth-token --show       # 把 token 填进 ~/.agx/agx_config.json 的 gateways[0].token", "ERROR")
        log("重跑时已安装的部分会自动复用，不会重复建 Agent。", "ERROR")
        log("本次继续执行后续 Phase，但系统 Agent 未注册 → 暂时无法接活。", "WARN")
        return False
    return True


def _get_per_host_unique_agents(install_src):
    """Read the per_host_unique=true agent list from agx_struct.json.
    Returns {agent_name: agent_id}, used by Phase 1d to generate the UUID suffix.
    """
    struct_path = Path(install_src) / "agx_struct.json"
    if not struct_path.exists():
        return {}
    try:
        with open(struct_path) as f:
            struct = json.load(f)
        return {a["agent_name"]: a["agent_id"] for a in struct.get("agents", []) if a.get("per_host_unique")}
    except Exception:
        return {}


def _md5_of_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_content(src, dst, sample=5):
    """Compare the contents of two entities (file or directory); returns (ok, reason).

    Directory: compare the file count + confirm each exists, then compare md5 of `sample` files.
               A full md5 on a directory such as skills (tens of MB) takes seconds, and sampling
               is enough to judge "was everything copied"; real corruption shows up in use and
               does not need install-time full verification.
    File: compare size + full md5.
    """
    if src.is_dir():
        src_files = sorted(p for p in src.rglob("*") if p.is_file())
        dst_files = sorted(p for p in dst.rglob("*") if p.is_file())
        if len(src_files) != len(dst_files):
            return False, f"文件数不符 目标{len(dst_files)}/源{len(src_files)}"
        for p in src_files:
            rel = p.relative_to(src)
            if not (dst / rel).is_file():
                return False, f"缺文件 {rel}"
        for p in src_files[:sample]:
            rel = p.relative_to(src)
            if _md5_of_file(p) != _md5_of_file(dst / rel):
                return False, f"内容不符 {rel}"
        return True, ""
    if not dst.is_file():
        return False, "目标不是普通文件"
    if os.path.getsize(src) != os.path.getsize(dst):
        return False, "大小不符"
    if _md5_of_file(src) != _md5_of_file(dst):
        return False, "md5 不符"
    return True, ""


# renaming a directory entry over an old symlink necessarily raises ENOTDIR, so it always lands in the fallback.
# this list exists only so tests can verify the path — a test that checks the result but not the path goes
# silent when someone changes the rename strategy (that is how S3/S4 passed back then).
_COPY_FALLBACK_HITS = []


def _copy_item_atomic(src, dst):
    """Copy src to dst; when dst already exists (typically an old symlink) replace it; returns (ok, reason).

    * Fixed the 2026-11-02 (TD-E) window: the old implementation was "unlink, then copy" — after
      the unlink and before the copy the item does not exist; if the copy failed (disk full /
      permissions / source busy) the agent kept missing that file and only a WARN was left behind.

    * The three paths differ — do not mix them up (measured on macOS, 2026-11-02):
      · target absent (fresh)      -> plain rename, one step, no window.
      · file item upgrading a link -> os.replace in one step. rename does not follow a final
                                      symlink, so the link itself is replaced and there is never
                                      a moment when dst is absent.
      · directory item upgrading a link -> **cannot use the above**. POSIX rename(2) returns
                                      ENOTDIR when old is a directory and new is not (a symlink
                                      does not count as a directory), so it must fall into the
                                      fallback: delete the link, then rename. That has a
                                      microsecond window, and rollback is implemented (readlink
                                      remembers where it pointed before deleting).
      The failure path guarantees no .tde_tmp is left behind and the original link is restored
      as closely as possible.
    """
    tmp = dst.parent / (dst.name + ".tde_tmp")

    def _clean_tmp():
        try:
            if tmp.is_symlink() or tmp.is_file():
                tmp.unlink()
            elif tmp.is_dir():
                shutil.rmtree(tmp)
        except Exception:
            pass

    try:
        # tmp left over from an interrupted run: clear it first, otherwise copytree fails because the target already exists
        _clean_tmp()

        if src.is_dir():
            shutil.copytree(src, tmp)
        else:
            shutil.copy2(src, tmp)

        ok, why = _same_content(src, tmp)
        if not ok:
            raise RuntimeError(f"拷贝校验失败: {why}")

        try:
            os.replace(str(tmp), str(dst))
        except OSError:
            # a directory entry upgrading a symlink always reaches here (ENOTDIR is not a defect, it is rename semantics).
            # make it recoverable: remember the target before deleting the link, and rebuild the link if the second rename fails —
            # otherwise "original link gone + tmp cleared" = the entry disappears entirely, violating the one hard requirement.
            _COPY_FALLBACK_HITS.append(str(dst))
            link_target = os.readlink(dst) if dst.is_symlink() else None
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            elif dst.is_dir():
                shutil.rmtree(dst)
            try:
                os.replace(str(tmp), str(dst))
            except Exception as e2:
                if link_target is not None:
                    try:
                        os.symlink(link_target, dst)
                    except Exception as e3:
                        # rollback failing too means the original link is really lost. Say it loudly, never swallow it.
                        log(
                            f"回滚软链失败 {dst}（原指向 {link_target}）: {e3}",
                            "ERROR",
                        )
                raise RuntimeError(f"替换失败（已尝试回滚原链）: {e2}")
        return True, ""
    except Exception as e:
        _clean_tmp()
        return False, str(e)


def sync_files(install_src, workdir, mode, env_cfg, dry_run=False):
    """Copy Agent config files + scripts + dependency files (real files, no symlinks)"""
    log_step("Phase 5: 文件同步与依赖下发")

    install_src = Path(install_src)
    workdir = Path(workdir)
    agents_dir = workdir / resolve_agents_dir_name(workdir)

    if dry_run:
        log("[DRY RUN] 跳过文件同步")
        return

    # Step 5a: copy Agent config files into the workspace
    agent_src_dir = install_src / "agents"
    per_host_unique_ids = env_cfg.get("_per_host_unique_ids", {})
    for agent_name in SYSTEM_AGENTS:
        if mode == "host" and agent_name not in per_host_unique_ids and agent_name not in HOST_AGENT_NAMES:
            continue
        # with per_host_unique the directory name is the unique id (e.g. agx_host_srv_a)
        agent_dir_name = per_host_unique_ids.get(agent_name, agent_name)
        # * fixed 2026-10-25: path joining must go through the single point agent_home_rel_dir(),
        #   otherwise host-type agents land on agx_agents/<id>/ instead of agx_agents/host/<id>/.
        ws_dir = agents_dir / agent_home_rel_dir(agent_name, agent_dir_name, agents_dir.name) / "workspace"
        # * fixed 2026-11-23 (exposed by the 138 clean-machine acceptance): this used to be `if not ws_dir.exists(): continue`,
        #   silently skipping a missing directory — and "the directory does not exist" is precisely the norm on a fresh install:
        #   `~/.agx/agx_agents/` holds no agent directory yet. The result: Phase 5 reported "dependency files
        #   copied", not one file existed for the four system Agents, and the first dispatch through OpenClaw hit
        #   WorkspaceVanishedError (the workspace was judged "vanished after initialization").
        #   Now: create the directory and fill in the files when missing, and log that fact.
        if not ws_dir.exists():
            ws_dir.mkdir(parents=True, exist_ok=True)
            log(f"  {agent_name}: workspace 缺失，已创建并下发配置", "WARN")

        # copy from agents/<name>/
        # (the old templates/ fallback was removed on 2026-10-24: the template mechanism became
        #   cloning agents/agent_template, and everything under templates/ has been moved into it)
        custom_dir = agent_src_dir / agent_name
        for fname in AGENT_CONFIG_FILES:
            src = custom_dir / fname
            if src.exists():
                shutil.copy2(src, ws_dir / fname)

        # copy extra files such as HEARTBEAT.md / agx.env / SUMMARY.md
        for fname in AGENT_EXTRA_FILES:
            src = custom_dir / fname
            if src.exists():
                shutil.copy2(src, ws_dir / fname)
            elif fname == "SUMMARY.md":
                # SUMMARY.md needs at least an empty file; the Agent maintains it after startup
                (ws_dir / fname).write_text("# SUMMARY.md — 工作摘要\n")

        notices_src = custom_dir / "notices"
        if notices_src.exists():
            notices_dst = ws_dir / "notices"
            notices_dst.mkdir(exist_ok=True)
            for nf in notices_src.iterdir():
                if nf.is_file():
                    shutil.copy2(nf, notices_dst / nf.name)

        log(f"已拷贝 {agent_name} 配置文件到 workspace")

        # per_host_unique agent: write agx.env back so the agent_id is uniquified
        unique_id = per_host_unique_ids.get(agent_name, "")
        if unique_id:
            env_path = ws_dir / "agx.env"
            lines = []
            if env_path.exists():
                lines = env_path.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("AGX_AGENT_ID="):
                    new_lines.append(f'AGX_AGENT_ID="{unique_id}"')
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f'AGX_AGENT_ID="{unique_id}"')
            env_path.write_text("\n".join(new_lines) + "\n")
            log(f"  agx.env 已更新: AGX_AGENT_ID={unique_id}")

    # Step 5b: copy scripts into workdir
    scripts_src = install_src / "scripts"
    scripts_dst = workdir / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    for f in scripts_src.iterdir():
        if f.is_file():
            shutil.copy2(f, scripts_dst / f.name)
    log("已拷贝 scripts/ 到工作目录")

    # Step 5b2: copy the start/stop scripts into workdir (uniform across platforms)
    START_STOP_SCRIPTS = [
        "start_daemon.sh",
        "start_daemon_host.sh",
        "stop_daemon.sh",
        "start_daemon.bat",
        "start_daemon_host.bat",
        "stop_daemon.bat",
    ]
    for name in START_STOP_SCRIPTS:
        src = install_src / name
        if src.exists():
            shutil.copy2(src, workdir / name)
    log("已拷贝启停脚本到工作目录")

    # Step 5b3: incremental docs/ sync (* added 2026-10-24)
    #
    # Background: the copytree in Phase 3 above copies whole directories and is guarded by `if not dst.exists()`.
    #       For an [already installed environment] workdir/docs/ exists -> the whole directory is skipped ->
    #       docs sub-files added to the repo afterwards never reach production.
    #       Real case: docs/internal/watchdog-openclaw.md existed in the repo but not in production,
    #       so the watchdog alert's template_file pointed at an empty path.
    #
    # Strategy: fill in only [missing] docs files, never overwrite existing ones (so hand edits on production survive).
    if (install_src / "docs").is_dir():
        docs_src = install_src / "docs"
        docs_dst = workdir / "docs"
        added = 0
        for f in sorted(docs_src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(docs_src)
            # skip runtime artifacts / log archives (large, no deployment value)
            if rel.parts and rel.parts[0] in ("agent_docs", ".DS_Store"):
                continue
            if f.name == ".DS_Store":
                continue
            target = docs_dst / rel
            if target.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                added += 1
            except Exception as e:
                log(f"docs 同步失败 {rel}: {e}", "WARN")
        if added:
            log(f"docs 补充 {added} 个文件（仅补缺失，不覆盖）")

    # Step 5c: ship the dependency files (atomic replace; no more symlinks)
    # * Lao Feng, 2026-11-02 (TD-E): always real copies — copytree for directories, copy2 for files.
    # * fixed the 2026-11-02 window: the old implementation was "unlink, then copy"; after the unlink and before
    #   the copy the item does not exist; a failure (disk full / permissions / source busy) left the agent's
    #   docs/internal/project-management.md and friends missing with only a WARN.
    #   Now it is tmp + verify + rename (details in _copy_item_atomic):
    #     (1) dst is a symlink -> copy to <dst>.tde_tmp (the original link stays usable)
    #     (2) verify contents (directories: file count + sampled md5; files: size + md5) — on failure delete the tmp, leave the link
    #     (3) pass -> os.replace(tmp, dst). File items are one step (rename does not follow a final
    #        symlink); directory items must take the fallback because of rename's ENOTDIR (delete the link, then
    #        rename, microsecond window, rollback implemented). See _copy_item_atomic
    #     (4) any step raising -> clear the tmp, original link untouched
    #   Same semantics as the one-off migration script assistants/agx_dev/notes/migrate_tde_symlink_to_copy.py,
    #   so the two no longer diverge.
    #   Semantics:
    #     · dst is a symlink   -> atomically replace with a real copy (an old environment converts on the next install)
    #     · dst is a real file -> skip (idempotent; do not overwrite what the agent changed)
    for agent_name in SYSTEM_AGENTS:
        if mode == "host" and agent_name not in per_host_unique_ids and agent_name not in HOST_AGENT_NAMES:
            continue
        agent_dir_name = per_host_unique_ids.get(agent_name, agent_name)
        # * fixed 2026-10-25: use the single point agent_home_rel_dir() (host-type agents go under host/)
        ws = agents_dir / agent_home_rel_dir(agent_name, agent_dir_name, agents_dir.name) / "workspace"
        if not ws.exists():
            continue

        for item in SYMLINK_ITEMS:
            src = workdir / item
            dst = ws / item
            if not src.exists():
                continue

            if dst.is_symlink():
                ok, why = _copy_item_atomic(src, dst)
                if ok:
                    log(f"迁移软链 → 实拷贝: {item} ({agent_name})")
                else:
                    log(f"迁移软链失败 {item} → {agent_name}（旧软链保持不动）: {why}", "WARN")
                continue
            if dst.exists():
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            ok, why = _copy_item_atomic(src, dst)
            if not ok:
                log(f"拷贝 {item} → {agent_name}: {why}", "WARN")

    log("依赖文件拷贝完成")

    # Step 5d: remove BOOTSTRAP.md (generated at OpenClaw registration; confuses Agent behaviour)
    for agent_name in SYSTEM_AGENTS:
        if mode == "host" and agent_name not in per_host_unique_ids and agent_name not in HOST_AGENT_NAMES:
            continue
        agent_dir_name = per_host_unique_ids.get(agent_name, agent_name)
        # * fixed 2026-10-25: use the single point agent_home_rel_dir() (host-type agents go under host/)
        bootstrap_file = (
            agents_dir
            / agent_home_rel_dir(agent_name, agent_dir_name, agents_dir.name)
            / "workspace"
            / "BOOTSTRAP.md"
        )
        if bootstrap_file.exists():
            bootstrap_file.unlink()
            log(f"已删除 {agent_name}/BOOTSTRAP.md")
    log("BOOTSTRAP.md 清理完成")


# ═══════════════════════════════════════════════════════════════
# Phase 6: database initialization
# ═══════════════════════════════════════════════════════════════


def _split_sql_statements(ddl: str) -> list[str]:
    """Split SQL text on semicolons, but not semicolons inside string literals, backticked identifiers or comments.

    * Why ddl.split(";") must not be used:
      agx.sql's column comments themselves contain semicolons (a ``COMMENT '...; ...'``).
      A naive split cuts one CREATE TABLE in half, both halves are invalid statements, and the
      exception is swallowed into a WARN — so a table is silently not created, until Phase 7
      reports a completely unrelated error.
      (Measured in the 2026-11-23 empty-database drill: only 14 of 21 tables were created; this was the cause.)
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    in_line_comment = False
    in_block_comment = False
    i, n = 0, len(ddl)
    while i < n:
        ch = ddl[i]
        nxt = ddl[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == "\\" and nxt:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
        elif ch == "#":
            in_line_comment = True
            buf.append(ch)
        elif ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def init_database(workdir, env_cfg, dry_run=False):
    """MySQL database creation + DDL + system_project (runs in-process)"""
    log_step("Phase 6: 数据库初始化")

    db = _db_config(env_cfg)
    network_id = env_cfg.get("AGX_NETWORK_ID", "lan_001")

    if dry_run:
        log(f"[DRY RUN] 将连接 {db.host}:{db.port}/{db.db_name}")
        return

    # 1. Check connectivity
    try:
        conn = pymysql.connect(
            host=db.host, port=db.port, user=db.user, password=db.password, charset="utf8mb4", autocommit=True
        )
        log("MySQL 连接正常")
    except Exception as e:
        log(f"MySQL 连接失败: {e}", "ERROR")
        return

    try:
        # 2. Create database
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE IF NOT EXISTS `{db.db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        log(f"database '{db.db_name}' ready")

        # 3. Import DDL
        conn.select_db(db.db_name)
        sql_file = Path(workdir) / "agx.sql"
        if not sql_file.exists():
            log(f"agx.sql 不存在: {sql_file}", "WARN")
        else:
            try:
                with conn.cursor() as c:
                    c.execute("SELECT 1 FROM agx_project LIMIT 1")
                log("tables exist, skip DDL")
            except Exception:
                ddl = sql_file.read_text()
                expected = len(re.findall(r"CREATE TABLE", ddl, re.I))
                failed: list[str] = []
                for stmt in _split_sql_statements(ddl):
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    # drop pure comment blocks: whole segments starting with -- or /* and containing no real statement
                    bare = re.sub(r"/\*.*?\*/", "", stmt, flags=re.S)
                    bare = "\n".join(l for l in bare.splitlines() if not l.strip().startswith("--"))
                    if not bare.strip():
                        continue
                    try:
                        with conn.cursor() as c:
                            c.execute(stmt)
                    except Exception as e2:
                        msg = str(e2)
                        if "Duplicate" in msg or "already exists" in msg:
                            continue
                        failed.append(msg.splitlines()[0][:160])
                if failed:
                    log(f"DDL import: {len(failed)} 条语句失败（表可能没建全）", "ERROR")
                    for msg in failed[:5]:
                        log(f"  {msg}", "ERROR")
                    log("请跑 python3 scripts/verify_schema.py 核对，或在干净库上重试", "ERROR")
                else:
                    # close with a number that can be checked, instead of just saying done
                    with conn.cursor() as c:
                        c.execute(
                            "SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema=DATABASE() AND table_name LIKE 'agx\\_%'"
                        )
                        made = c.fetchone()[0]
                    log(f"DDL import done: {made} 张 agx_* 表（DDL 声明 {expected} 张）")
                    if made < expected:
                        log("表数少于 DDL 声明，可能有语句未被导入", "WARN")

        # 4. Create system_project
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM agx_project WHERE project_id='system_project' LIMIT 1")
            if not c.fetchone():
                c.execute(
                    "INSERT INTO agx_project (network_id, project_id, project_name, x_desc, admin_agent_id) VALUES (%s,%s,%s,%s,%s)",
                    (network_id, "system_project", "system", "system builtin project", SYS_CORE),
                )
                log("system_project created")
            else:
                log("system_project exists")

        # 5. Set built-in agent permissions (per_host_unique agents use LIKE matching)
        # read struct to get the base_id of a per_host_unique agent
        struct_path = Path(workdir) / "agx_struct.json"
        unique_base_ids = []
        if struct_path.exists():
            try:
                with open(struct_path) as f:
                    struct_data = json.load(f)
                unique_base_ids = [a["agent_id"] for a in struct_data.get("agents", []) if a.get("per_host_unique")]
            except Exception:
                pass
        like_clauses = " OR ".join([f"agent_id LIKE '{bid}\\_%' OR agent_id = '{bid}'" for bid in unique_base_ids])
        _sys_in = ",".join(f"'{n}'" for n in (SYS_CORE, SYS_AGENTFATHER, SYS_PROJECTMOTHER))
        _admin_cond = " OR ".join(
            f"agent_id = '{n}'" for n in (SYS_CORE, SYS_PROJECTMOTHER)
        )
        AGENT_SQL = (
            "UPDATE agx_agent_registry "
            "SET scope = 'system', "
            f"is_project_admin = CASE WHEN {_admin_cond} THEN 1 ELSE 0 END "
            f"WHERE agent_id IN ({_sys_in})"
            + (f" OR {like_clauses}" if like_clauses else "")
        )
        with conn.cursor() as c:
            c.execute(AGENT_SQL)
        log("agent permissions set")

        log("database init done")
    finally:
        conn.close()


def _delete_gateway_agent(env_cfg, agent_id):
    """Delete the given agent through Gateway WS (synchronous wrapper)."""
    import asyncio

    async def _do_delete():
        try:
            import websockets

            ws_url = env_cfg.get("AGX_GATEWAY_WS_URL", "ws://127.0.0.1:18789")
            token = env_cfg.get("AGX_GATEWAY_TOKEN", "")
            for k in list(os.environ):
                if k.lower().endswith("_proxy"):
                    del os.environ[k]
            ws = await websockets.connect(ws_url, proxy=None)
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "conn",
                        "method": "connect",
                        "params": {
                            "minProtocol": 3,
                            "maxProtocol": 4,
                            "client": {"id": "installer", "version": "0.1", "platform": "python", "mode": "backend"},
                            "role": "operator",
                            "scopes": ["operator.read", "operator.write", "operator.admin"],
                            "caps": [],
                            "commands": [],
                            "permissions": {},
                            "auth": {"token": token},
                            "locale": "zh-CN",
                            "userAgent": "agx-install",
                        },
                    }
                )
            )
            resp = await _recv_response(ws)
            if not (resp.get("ok") and resp.get("payload", {}).get("type") == "hello-ok"):
                log(f"  Gateway 认证失败，跳过删除 {agent_id}", "WARN")
                await ws.close()
                return
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "d",
                        "method": "agents.delete",
                        "params": {"agentId": agent_id},
                    }
                )
            )
            resp = await _recv_response(ws, timeout=10)
            if resp.get("ok"):
                log(f"  已删除: {agent_id}")
            else:
                log(f"  删除 {agent_id} 失败: {resp.get('error', '')}", "WARN")
            await ws.close()
        except Exception as e:
            log(f"  删除 {agent_id} 异常: {e}", "WARN")

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.run(_do_delete())
        return
    asyncio.ensure_future(_do_delete())


def _write_agx_env(workdir, agent_name, agent_id, per_host_unique_ids=None):
    """Write agx.env back, ensuring AGX_AGENT_ID points at the correct unique id.
    With per_host_unique the directory name is the unique id (e.g. agx_host_srv_a); otherwise the agent_name is used.
    """
    dir_name = (per_host_unique_ids or {}).get(agent_name, agent_name)
    # * 2026-10-25: the directory name supports the new structure (agx_agents) + the host/ subdirectory
    _rel = agent_home_rel_dir(agent_name, dir_name, resolve_agents_dir_name(workdir))
    env_path = Path(workdir) / resolve_agents_dir_name(workdir) / _rel / "workspace" / "agx.env"
    if not env_path.parent.exists():
        return
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    new_lines = []
    found = False
    for line in lines:
        if line.startswith("AGX_AGENT_ID="):
            new_lines.append(f'AGX_AGENT_ID="{agent_id}"')
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f'AGX_AGENT_ID="{agent_id}"')
    env_path.write_text("\n".join(new_lines) + "\n")
    log(f"  agx.env 已更新: AGX_AGENT_ID={agent_id}")


# ═══════════════════════════════════════════════════════════════
# cascade delete of an Agent (install.py internal tool, not exposed over MCP)
# ═══════════════════════════════════════════════════════════════


def cleanup_agent(agent_id, env_cfg=None):
    """Transaction-wrapped full cascade delete — registry + tasks + group membership + Redis.

    Only used inside install.py, never exposed over MCP, to avoid uncontrollable external calls.
    Design goal: idempotent and safe — no error even when some data was already deleted by hand.

    Args:
        agent_id: the Agent ID to clean up (e.g. agx_host_srv_a)
        env_cfg: environment config dict (optional); when passed, Redis is cleaned up too

    Returns:
        dict: {"deleted": {"registry": N, "tasks": N, "group_members": N, "redis": bool}}
    """
    from common.mysql.mysqlconn import UsingMysql

    result = {"deleted": {"registry": 0, "tasks": 0, "group_members": 0, "redis": False}}

    # ── Phase 1: collect related data (read before deleting, to stay idempotent) ──
    task_ids = []
    group_ids = []
    network_id = None
    with UsingMysql() as um:
        um.cursor.execute("SELECT task_id FROM agx_task_base WHERE agent_id = %s", (agent_id,))
        task_ids = [r["task_id"] for r in um.cursor.fetchall()]

        um.cursor.execute(
            "SELECT group_id FROM agx_agent_group_member WHERE agent_id = %s",
            (agent_id,),
        )
        group_ids = [r["group_id"] for r in um.cursor.fetchall()]

        um.cursor.execute("SELECT network_id FROM agx_agent_registry WHERE agent_id = %s", (agent_id,))
        row = um.cursor.fetchone()
        if row:
            network_id = row.get("network_id")

    # ── Phase 2: transactional write (single connection; __exit__ guarantees commit/rollback) ──
    with UsingMysql(commit=True) as um:
        try:
            um.cursor.execute("DELETE FROM agx_task_base WHERE agent_id = %s", (agent_id,))
            result["deleted"]["tasks"] = um.cursor.rowcount

            um.cursor.execute("DELETE FROM agx_agent_group_member WHERE agent_id = %s", (agent_id,))
            result["deleted"]["group_members"] = um.cursor.rowcount

            um.cursor.execute("DELETE FROM agx_agent_registry WHERE agent_id = %s", (agent_id,))
            result["deleted"]["registry"] = um.cursor.rowcount
        except Exception:
            raise  # __exit__ rolls back automatically when it detects an exception

    # ── Phase 3: Redis cleanup (best-effort; degrade silently on failure) ──
    try:
        if env_cfg:
            import redis as redis_mod

            redis_host = env_cfg.get("AGX_REDIS_HOST", "127.0.0.1")
            redis_port = int(env_cfg.get("AGX_REDIS_PORT", 6379))
            redis_db = int(env_cfg.get("AGX_REDIS_DB", 0))
            redis_pass = env_cfg.get("AGX_REDIS_PASSWORD", "") or None
            redis_user = env_cfg.get("AGX_REDIS_USERNAME", "default")
            r = redis_mod.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                username=redis_user,
                password=redis_pass,
                decode_responses=True,
            )
            pipe = r.pipeline()

            # Agent main cache + index
            pipe.delete(f"agx:entity:agent:{agent_id}")
            pipe.zrem("agx:idx:agent:all", agent_id)
            pipe.srem("agx:idx:agent:active", agent_id)
            if network_id:
                pipe.srem(f"agx:idx:agent:by_network:{network_id}", agent_id)

            # task cache + index
            for tid in task_ids:
                pipe.delete(f"agx:entity:task:{tid}")
                pipe.srem("agx:idx:task:active", tid)
                pipe.zrem("agx:idx:task:all", tid)
            pipe.delete(f"agx:idx:task:by_agent:{agent_id}")

            # group membership cache + index
            for gid in group_ids:
                pipe.delete(f"agx:entity:agent_group_member:{gid}:{agent_id}")
            pipe.delete(f"agx:idx:agent_group_member:by_agent:{agent_id}")

            pipe.execute()
            result["deleted"]["redis"] = True
    except Exception:
        pass  # degrade silently when Redis is unavailable

    return result


def sync_global_struct(install_src, workdir, env_cfg, dry_run=False, host_only=False):
    """Sync agx_struct.json -> MySQL (inline replacement for scripts/sync_struct.py)
    With host_only=True only per_host_unique agents are synced (host mode only).
    """
    from common.mysql.mysqlservice import (
        getRowByPk,
        getRowByPkOne,
        getRowsByCondition,
        getRowsBySQl,
        saveRow,
        updateBySql,
        updateRow,
        updateRowByPk,
    )

    struct_path = Path(workdir) / "agx_struct.json"
    if not struct_path.exists():
        log("agx_struct.json 不存在，跳过结构同步", "WARN")
        return

    with open(struct_path) as f:
        struct = json.load(f)

    network_id = env_cfg.get("AGX_NETWORK_ID", "lan_001")
    host_id = env_cfg.get("AGX_HOST_ID", "")

    if dry_run:
        log("[DRY RUN] 跳过结构同步")
        return

    def _load_md(file_path):
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except Exception:
            return ""

    def _backfill_md(agent_id, fields):
        """* DH-068: self-healing for existing data — backfill [empty values] only, never overwrite non-empty content.

        Principle: a non-empty md in the DB may be the production source of truth (production >
        DB > repo) adjusted by hand; overwriting it would be a disaster. So this only fills in
        blanks, never writes back, never touches non-empty fields.
        Returns the list of field names actually backfilled (for log / receipt verification).
        """
        cols = ",".join(fields.keys())
        row = getRowByPk("agx_agent_registry", cols, "agent_id", agent_id)
        if not row:
            return []
        updates = {}
        for k, v in fields.items():
            if not (v or "").strip():
                continue  # the template itself is empty -> there is no real value to backfill
            cur = row.get(k)
            if cur is None or not str(cur).strip():
                updates[k] = v
        if updates:
            updateRowByPk("agx_agent_registry", updates, "agent_id", agent_id)
        return sorted(updates)

    # ── Agents ──
    install_src = Path(__file__).resolve().parent
    per_host_unique_ids = env_cfg.get("_per_host_unique_ids", {})

    # on a host_only reinstall, clean up the old agent and its related data
    # search across hosts (do not miss orphans); isolate host_id in Python (do not delete other hosts)
    if host_only:
        for base_name, new_id in per_host_unique_ids.items():
            like_pattern = f"{base_name}\\_%%"  # an f-string emits %% -> pymysql escapes it -> MySQL receives a single %
            agents = getRowsBySQl(
                f"SELECT agent_id, host_id FROM agx_agent_registry WHERE agent_id LIKE '{like_pattern}'",
                {"active_status": "1"},
                "",
                "",
            )
            for a in agents or []:
                old_aid = a["agent_id"]
                old_host = a.get("host_id", "")
                # safety isolation: an explicit host_id that is not this machine's -> skip, protecting cross-host agents
                if old_host and old_host != host_id:
                    continue
                if old_aid != new_id:
                    r = cleanup_agent(old_aid, env_cfg)
                    reason = f"host={old_host or '空'}"
                    log(
                        f"  清理: {old_aid} ({reason}) → registry={r['deleted']['registry']} tasks={r['deleted']['tasks']} group={r['deleted']['group_members']} redis={r['deleted']['redis']}"
                    )

    for a in struct.get("agents", []):
        aid = a["agent_id"]
        name = a["agent_name"]

        # host_only mode: handle only per_host_unique agents
        if host_only and name not in per_host_unique_ids:
            continue

        # per_host_unique agent: use the unique id generated at install time
        if name in per_host_unique_ids:
            unique_id = per_host_unique_ids[name]
            if not unique_id or unique_id == a.get("agent_id"):
                # no unique id generated, or it equals the bare name (the earlier logic failed to produce a suffix) -> generate it on the spot
                unique_id = f"{a['agent_id']}_{_sanitize_host_suffix(env_cfg.get('AGX_HOST_ID', ''))}"
                per_host_unique_ids[name] = unique_id
                env_cfg["_per_host_unique_ids"] = per_host_unique_ids
                log(f"  {name} 补后缀唯一 ID: {unique_id}")
            base_id = a["agent_id"]
            # check whether this machine already has a record of that agent (* 2026-11-11, across id prefix families)
            #   Background: a host agent carries the host suffix agx_host_<host>; looking it up by base_id alone
            #   would treat a renamed machine as "not present" -> creating a duplicate host_manager, and
            #   agx_resolve_host_manager becomes ambiguous. Hence:
            #     (1) first match exactly on {prefix}_{host_suffix} (several hosts sharing a prefix do not collide)
            #     (2) then fall back to a family LIKE + host_id filter (compatible with the early random-hex suffixes)
            _pfx_family = _PER_HOST_ID_FAMILIES.get(name) or (base_id,)
            _sfx = _sanitize_host_suffix(host_id or "")
            _exact = "','".join(f"{p}_{_sfx}" for p in _pfx_family)
            existing = None
            if host_id:
                existing = getRowByPkOne(
                    f"SELECT agent_id, host_id FROM agx_agent_registry "
                    f"WHERE agent_id IN ('{_exact}') AND host_id = '{host_id}' AND active_status = 1"
                )
            if not existing and host_id:
                _likes = " OR ".join(f"agent_id LIKE '{p}\\_%'" for p in _pfx_family)
                existing = getRowByPkOne(
                    f"SELECT agent_id, host_id FROM agx_agent_registry "
                    f"WHERE ({_likes}) AND host_id = '{host_id}' AND active_status = 1 "
                    f"ORDER BY pkid LIMIT 1"
                )
            if existing:
                per_host_unique_ids[name] = existing["agent_id"]
                env_cfg["_per_host_unique_ids"][name] = existing["agent_id"]
                log(f"  Agent {name} ({existing['agent_id']}) 已存在（复用）")
                _write_agx_env(workdir, name, existing["agent_id"], per_host_unique_ids)
                # * DH-068: the old implementation just continue'd here and never touched the md ->
                #   once a wrongly computed template directory left an empty md behind, [a reinstall could not fix it].
                #   Here we add one md sync that only fills in blanks, so existing data can heal itself.
                _md_fields, _tpl_dir = load_agent_md_bundle(
                    install_src, a, existing["agent_id"], name, host_id
                )
                _filled = _backfill_md(existing["agent_id"], _md_fields)
                if _filled:
                    log(
                        f"  Agent {name} ({existing['agent_id']}) md 空值已回填: "
                        f"{', '.join(_filled)} | 模板={_tpl_dir}"
                    )
                continue
            # check for a leftover suffixless record
            legacy = getRowByPk("agx_agent_registry", "agent_id,host_id", "agent_id", base_id)
            if legacy:
                legacy_host = legacy.get("host_id")
                if not legacy_host or legacy_host == host_id:
                    updateBySql(f"UPDATE agx_agent_registry SET agent_id = '{unique_id}' WHERE agent_id = '{base_id}'")
                    per_host_unique_ids[name] = unique_id
                    env_cfg["_per_host_unique_ids"][name] = unique_id
                    log(f"  Agent {name} 迁移: {base_id} → {unique_id}")
                    _write_agx_env(workdir, name, unique_id, per_host_unique_ids)
                    continue
            aid = unique_id
        else:
            # a non-per_host_unique agent keeps its original name
            row = getRowByPk("agx_agent_registry", "host_id, deploy_status", "agent_id", aid)
            if row:
                deploy_status = a.get("deploy_status", "deployed")
                updates = {"deploy_status": deploy_status}
                existing_host = row.get("host_id")
                if not existing_host and host_id:
                    updates["host_id"] = host_id
                    log(f"  Agent {name} ({aid}) host_id 已修复: NULL → {host_id}")
                updateRowByPk("agx_agent_registry", updates, "agent_id", aid)
                log(f"  Agent {name} ({aid}) 已存在，同步: {', '.join(f'{k}={v}' for k, v in updates.items())}")
                continue
            # struct marks per_host_unique but per_host_unique_ids is empty -> generate a unique id as a fallback
            if a.get("per_host_unique"):
                unique_id = f"{aid}_{_sanitize_host_suffix(env_cfg.get('AGX_HOST_ID', ''))}"
                per_host_unique_ids[name] = unique_id
                env_cfg["_per_host_unique_ids"] = per_host_unique_ids
                log(f"  {name} 兜底唯一 ID: {aid} → {unique_id}")
                aid = unique_id

        # agent_labs: struct may be null (e.g. a host agent has a different name per host, with no fixed labs directory)
        # store it faithfully as struct says (null stays null); when resolving the template directory,
        # load_agent_md_bundle falls back to struct's agent_id uniformly.
        # * DH-068: the fallback target is the [template base name], not aid (under per_host_unique, aid is the instance id).
        labs_db = a.get("agent_labs")

        # template directory resolution + 5 md loads + identity rendering (with WARNs for missing/empty), collapsed into one point.
        # the old templates/ fallback was removed on 2026-10-24 (the template mechanism is now
        # cloning agents/agent_template).
        _md_fields, custom_dir = load_agent_md_bundle(install_src, a, aid, name, host_id)
        agents_md = _md_fields["agents_md"]
        soul_md = _md_fields["soul_md"]
        identity_md = _md_fields["identity_md"]
        user_md = _md_fields["user_md"]
        tools_md = _md_fields["tools_md"]

        saveRow(
            "agx_agent_registry",
            {
                "network_id": network_id,
                "agent_id": aid,
                "agent_name": name,
                "description": a.get("description", ""),
                "x_desc": a.get("description", ""),
                "agent_role": a.get("role", "worker"),
                "base_model": a.get("model", "deepseek-chat"),
                "system_prompt": a.get("prompt", ""),
                "host_id": host_id,
                "project_id": "system_project",
                "active_status": 1,
                "scope": a.get("scope", "project"),
                "is_project_admin": a.get("is_project_admin", 0),
                "agent_labs": labs_db,
                "agents_md": agents_md,
                "soul_md": soul_md,
                "identity_md": identity_md,
                "user_md": user_md,
                "tools_md": tools_md,
                "default_session_id": f"agent:{aid}:main",
                "deploy_status": a.get("deploy_status", "pending"),
            },
        )
        log(f"  Agent {name} ({aid}) 已创建")

        # ── create shared tasks (is_private=0, driven by agx_struct.json) ──
        cur_platform = a.get("platform", "openclaw")
        for t_def in struct.get("tasks", []):
            if t_def.get("is_private", 1) != 0:
                continue
            task_platforms = t_def.get("platforms", [])
            if task_platforms and "all" not in task_platforms and cur_platform not in task_platforms:
                continue

            t_labs = t_def["task_labs"]
            t_name = t_def["task_name"]
            rows = getRowsByCondition(
                "agx_task_base",
                "1",
                {"task_labs": t_labs, "agent_id": aid},
                "",
                "LIMIT 1",
            )
            task_existed = len(rows) > 0

            md_path = install_src / "tasks" / f"{t_labs}.md"
            x_desc = _load_md(md_path) or t_def.get("description", "")

            if not task_existed:
                tid = f"task_{os.urandom(6).hex()}"
                saveRow(
                    "agx_task_base",
                    {
                        "task_id": tid,
                        "task_name": t_name,
                        "task_labs": t_labs,
                        "agent_id": aid,
                        "network_id": network_id,
                        "description": t_def.get("description", ""),
                        "x_desc": x_desc,
                        "active_status": t_def.get("active_status", 1),
                        "is_private": 0,
                        "reply_mode": t_def.get("reply_mode", "result"),
                        "dedicated_session": 1 if t_def.get("dedicated_session") else 0,
                        "session_done_wake": t_def.get("session_done_wake", 1),
                    },
                )
                log(f"    共享任务 {t_labs} → {aid}")
                updateBySql(f"UPDATE agx_task_base SET update_dt=NOW() WHERE task_id='{tid}'")
            else:
                updateRow(
                    "agx_task_base",
                    {
                        "x_desc": x_desc,
                        "is_private": 0,
                        "active_status": t_def.get("active_status", 1),
                        "reply_mode": t_def.get("reply_mode", "result"),
                        "dedicated_session": 1 if t_def.get("dedicated_session") else 0,
                        "session_done_wake": t_def.get("session_done_wake", 1),
                    },
                    {"task_labs": t_labs, "agent_id": aid},
                )
                updateBySql(f"UPDATE agx_task_base SET update_dt=NOW() WHERE task_labs='{t_labs}' AND agent_id='{aid}'")
                log(f"    更新共享任务 {t_labs} → {aid}")

    # ── Groups ──
    for g in struct.get("agent_groups", []):
        gid = g["group_id"]
        if not getRowByPk("agx_agent_group", "1", "group_id", gid):
            saveRow(
                "agx_agent_group",
                {
                    "network_id": network_id,
                    "group_id": gid,
                    "group_name": g["group_name"],
                    "x_desc": g.get("description", ""),
                },
            )
            log(f"  Group {g['group_name']} ({gid}) 已创建")
        for mid in g.get("members", []):
            # host_only mode: add only per_host_unique agent members, replaced by their unique ids
            if host_only:
                if mid not in per_host_unique_ids:
                    continue
                mid = per_host_unique_ids[mid]
            rows = getRowsByCondition(
                "agx_agent_group_member",
                "1",
                {"group_id": gid, "agent_id": mid},
                "",
                "LIMIT 1",
            )
            if len(rows) == 0:
                saveRow(
                    "agx_agent_group_member",
                    {
                        "group_id": gid,
                        "agent_id": mid,
                        "network_id": network_id,
                    },
                )

    # ── Tasks ──
    # * fixed 2026-11-11: a per_host_unique owner must be mapped to [this machine's instance id],
    #   using the same mapping as agent records (per_host_unique_ids / _sanitize_host_suffix).
    #   The old implementation replaced owners only when host_only=True; a full sync (host_only=False) wrote
    #   struct's bare owner (e.g. "agx_host") straight into the DB -> creating orphan task rows that do not
    #   exist in the registry (measured: the three rows host_info / host_communication / agent_deploy in
    #   agx_task_base had agent_id='agx_host', so agx_host_test_host_mac could not get agent_deploy).
    _phu_by_struct_id = {
        a["agent_id"]: a["agent_name"] for a in struct.get("agents", []) if a.get("per_host_unique")
    }
    _host_sfx_for_tasks = _sanitize_host_suffix(host_id) if host_id else ""
    _orphan_owner_checked = set()

    def _resolve_task_owner(_owner):
        """Map a struct bare owner (agx_host) to the local instance id (agx_host_test_host_mac).

        The mapping source is the same as for agent records: (1) per_host_unique_ids (the instance
        ids produced or reused at install time) takes priority; (2) when missing, fall back to
        {bare_id}_{host_suffix}, never random hex. A non-per_host_unique owner is returned unchanged.
        """
        if not _owner:
            return _owner
        if _owner in per_host_unique_ids:  # owner is the template agent_name
            return per_host_unique_ids[_owner]
        _tpl_name = _phu_by_struct_id.get(_owner)  # owner is that template's struct bare agent_id
        if _tpl_name:
            _mapped = per_host_unique_ids.get(_tpl_name)
            if _mapped:
                return _mapped
            if _host_sfx_for_tasks:
                return f"{_owner}_{_host_sfx_for_tasks}"
        return _owner

    def _warn_orphan_owner(_base):
        """Report orphan rows once when the mapping applies (warn only; never delete or rewrite existing data)."""
        if not _base or _base in _orphan_owner_checked:
            return
        _orphan_owner_checked.add(_base)
        try:
            _rows = getRowsByCondition("agx_task_base", "task_labs", {"agent_id": _base}, "", "LIMIT 5")
        except Exception:
            return
        if _rows:
            _labs = ", ".join(r.get("task_labs", "?") for r in _rows)
            log(
                f"  ⚠ 检测到 agent_id='{_base}' 的孤儿任务行（{_labs}）——"
                f"registry 中无此 agent，需人工核对迁移",
                "WARN",
            )

    def _ensure_task_row(t, tlabs, owner_id):
        """Idempotently seed one task row per (task_labs, agent_id). Returns None when it exists, the new task_id otherwise."""
        # duplicate check: with an owner, look up by (task_labs, agent_id); without one, by task_labs
        if owner_id:
            rows = getRowsByCondition("agx_task_base", "1", {"task_labs": tlabs, "agent_id": owner_id}, "", "LIMIT 1")
            if len(rows) > 0:
                return None
        elif getRowByPk("agx_task_base", "1", "task_labs", tlabs):
            return None

        md_path = install_src / t.get("md_file", f"tasks/{tlabs}.md")
        x_desc = _load_md(md_path) or t.get("description", "")
        tid = f"task_{os.urandom(6).hex()}"
        saveRow(
            "agx_task_base",
            {
                "task_id": tid,
                "task_name": t["task_name"],
                "task_labs": tlabs,
                "agent_id": owner_id,
                "owner_group": t.get("owner_group", "") or None,  # host condition: a task exclusive to a group binding
                "network_id": network_id,
                "x_desc": x_desc,
                "active_status": t.get("active_status", 1),
                "is_private": t.get("is_private", 0),
                "reply_mode": t.get("reply_mode", "result"),
                "dedicated_session": 1 if t.get("dedicated_session") else 0,
                "session_done_wake": t.get("session_done_wake", 1),
            },
        )
        log(f"  Task {t['task_name']} ({tlabs}) → {tid}  agent={owner_id or '(共享)'}")
        updateBySql(f"UPDATE agx_task_base SET update_dt=NOW() WHERE task_id='{tid}'")
        return tid

    for t in struct.get("tasks", []):
        tlabs = t["task_labs"]
        owner = t.get("owner_agent", "")

        # host_only mode: create only the tasks that belong to per_host_unique agents
        if host_only and not (owner in per_host_unique_ids or owner in _phu_by_struct_id):
            continue
        _resolved_owner = _resolve_task_owner(owner)
        if _resolved_owner != owner:
            log(f"    owner 映射: {owner} → {_resolved_owner}")
            _warn_orphan_owner(owner)
        owner = _resolved_owner

        # * exceptional ownership (Lao Feng, 2026-11-11):
        #   plan_execute is a PM-only task, but agx_core is the sole exception —
        #   it is the activation vehicle for the build-project / build-agent pipelines,
        #   and if _find_task(SYS_CORE, "plan_execute") cannot find it the code returns an error immediately,
        #   breaking both pipelines (measured 2026-11-11: without this row on agx_core, the pipelines are dead).
        #   So struct declares this exception explicitly with extra_owner_agents,
        #   and it is seeded back when agx_core is reinstalled or rebuilt; otherwise every reinstall silently loses it.
        _owners = [owner] if owner else [""]
        for _extra in t.get("extra_owner_agents", []) or []:
            if _extra and _extra not in _owners:
                _owners.append(_extra)

        for _own in _owners:
            _ensure_task_row(t, tlabs, _own)
    log("全局结构同步完成")


# ═══════════════════════════════════════════════════════════════
# Phase 7: local machine initialization
# ═══════════════════════════════════════════════════════════════


def init_system(workdir, env_cfg, dry_run=False):
    """Local machine initialization: register the host + deploy marker (runs in-process)"""
    log_step("Phase 7: 本机初始化")

    from common.mysql.mysqlconn import UsingMysql
    from common.mysql.mysqlservice import getRowByPk, saveRow, updateBySql

    network_id = env_cfg.get("AGX_NETWORK_ID", "lan_001")
    host_id = env_cfg.get("AGX_HOST_ID")

    if dry_run:
        log(f"[DRY RUN] will register host_id={host_id}")
        return

    try:
        # 1. Check core tables
        tables = ["agx_project", "agx_host_inventory", "agx_agent_registry", "agx_task_base"]
        with UsingMysql() as um:
            for tbl in tables:
                try:
                    um.cursor.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
                    log(f"  {tbl}: OK")
                except Exception:
                    log(f"{tbl} 不存在，请先执行数据库初始化", "ERROR")
                    return

        # 2. Register host
        hostname = socket.gethostname()
        local_ip = _get_local_ip()
        platform_label = _platform_label()

        row = getRowByPk("agx_host_inventory", "pkid", "host_id", host_id)
        if row:
            pkid = row["pkid"]
            log(f"host already registered: {host_id}")
        else:
            pkid = saveRow(
                "agx_host_inventory",
                {
                    "network_id": network_id,
                    "host_id": host_id,
                    "host_alias": host_id,
                    "endpoint_ip": local_ip,
                    "os_platform": platform_label,
                    "task_slots_limit": 16,
                    "active_status": 1,
                    "presence_status": 1,
                },
            )
            log(f"host registered: {host_id} ({hostname}, {local_ip}, {platform_label})")

        # 3. Sync host to Redis cache
        redis_host = env_cfg.get("AGX_REDIS_HOST", "127.0.0.1")
        redis_port = int(env_cfg.get("AGX_REDIS_PORT", 6379))
        redis_db = int(env_cfg.get("AGX_REDIS_DB", 0))
        redis_pass = env_cfg.get("AGX_REDIS_PASSWORD", "") or None
        redis_user = env_cfg.get("AGX_REDIS_USERNAME", "default")
        try:
            r = redis_mod.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                username=redis_user,
                password=redis_pass,
                decode_responses=True,
            )
            r.ping()
            pipe = r.pipeline()
            host_key = f"agx:entity:host:{host_id}"
            host_data = {
                "pkid": str(pkid),
                "host_id": host_id,
                "network_id": network_id,
                "host_alias": host_id,
                "endpoint_ip": local_ip,
                "os_platform": platform_label,
                "task_slots_limit": "16",
                "active_task_count": "0",
                "active_status": "1",
                "presence_status": "1",
            }
            pipe.hset(host_key, mapping=host_data)
            pipe.zadd("agx:idx:host:all", {host_id: int(pkid)})
            pipe.sadd(f"agx:idx:host:by_network:{network_id}", host_id)
            pipe.sadd("agx:idx:host:online", host_id)
            pipe.execute()
            log("Redis cache synced for host")
        except Exception as e:
            log(f"Redis 同步: {e}", "WARN")

        # 4. Create system_project if not exists (idempotent)
        row = getRowByPk("agx_project", "1", "project_id", "system_project")
        if not row:
            saveRow(
                "agx_project",
                {
                    "network_id": network_id,
                    "project_id": "system_project",
                    "project_name": "system",
                    "x_desc": "system builtin project",
                },
            )
            log("system_project created")

        # 5. Mark system agents as deployed (per_host_unique uses LIKE matching)
        struct_path = Path(workdir) / "agx_struct.json"
        struct_data = {"agents": []}
        if struct_path.exists():
            try:
                with open(struct_path) as f:
                    struct_data = json.load(f)
            except Exception:
                pass
        # read each agent's deploy_status from struct
        for a in struct_data.get("agents", []):
            ds = a.get("deploy_status", "deployed")
            base_id = a["agent_id"]
            if a.get("per_host_unique"):
                updateBySql(
                    f"UPDATE agx_agent_registry SET deploy_status='{ds}' WHERE agent_id LIKE '{base_id}\\_%' OR agent_id = '{base_id}'"
                )
            else:
                updateBySql(f"UPDATE agx_agent_registry SET deploy_status='{ds}' WHERE agent_id = '{base_id}'")
        log("system agents deploy_status synced from struct")

        # 6. Init marker
        # * marker file .agx_init_done.
        #   Old markers are no longer read (install rewrites it every time; leftover old files are harmless).
        marker = Path(workdir) / ".agx_init_done"
        marker.write_text(
            json.dumps(
                {"version": "1.0", "network_id": network_id, "host_id": host_id, "status": "completed"}, indent=2
            )
        )
        log(f"init marker: {marker}")

        log("system init done")
    except Exception as e:
        log(f"system init failed: {e}", "ERROR")


def _init_host_only(workdir, env_cfg, dry_run=False):
    """Lightweight host-node initialization: only register this machine into agx_host_inventory + Redis; no table structure, no system agents."""
    log_step("Phase 7: 本机初始化（host 模式）")

    from common.mysql.mysqlservice import getRowByPk, saveRow

    network_id = env_cfg.get("AGX_NETWORK_ID", "lan_001")
    host_id = env_cfg.get("AGX_HOST_ID")
    hostname = socket.gethostname()
    local_ip = _get_local_ip()
    platform_label = _platform_label()

    if dry_run:
        log(f"[DRY RUN] 将注册 host_id={host_id} 到数据库+Redis")
        return

    try:
        # 1. verify DB connectivity (table structure is not checked; core already built it)
        from common.mysql.mysqlservice import getSQL2Arr

        getSQL2Arr("SELECT 1", "1")
        log("MySQL 连接正常")

        # 2. register the host (idempotent)
        row = getRowByPk("agx_host_inventory", "pkid", "host_id", host_id)
        if row:
            pkid = row["pkid"]
            log(f"host 已注册: {host_id}")
        else:
            pkid = saveRow(
                "agx_host_inventory",
                {
                    "network_id": network_id,
                    "host_id": host_id,
                    "host_alias": host_id,
                    "endpoint_ip": local_ip,
                    "os_platform": platform_label,
                    "task_slots_limit": 16,
                    "active_status": 1,
                    "presence_status": 1,
                },
            )
            log(f"host 已注册: {host_id} ({hostname}, {local_ip})")

        # 3. sync to Redis
        redis_host = env_cfg.get("AGX_REDIS_HOST", "127.0.0.1")
        redis_port = int(env_cfg.get("AGX_REDIS_PORT", 6379))
        redis_db = int(env_cfg.get("AGX_REDIS_DB", 0))
        redis_pass = env_cfg.get("AGX_REDIS_PASSWORD", "") or None
        redis_user = env_cfg.get("AGX_REDIS_USERNAME", "default")
        try:
            r = redis_mod.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                username=redis_user,
                password=redis_pass,
                decode_responses=True,
            )
            r.ping()
            pipe = r.pipeline()
            host_key = f"agx:entity:host:{host_id}"
            host_data = {
                "pkid": str(pkid),
                "host_id": host_id,
                "network_id": network_id,
                "host_alias": host_id,
                "endpoint_ip": local_ip,
                "os_platform": platform_label,
                "task_slots_limit": "16",
                "active_task_count": "0",
                "active_status": "1",
                "presence_status": "1",
            }
            pipe.hset(host_key, mapping=host_data)
            pipe.zadd("agx:idx:host:all", {host_id: int(pkid)})
            pipe.sadd(f"agx:idx:host:by_network:{network_id}", host_id)
            pipe.sadd("agx:idx:host:online", host_id)
            pipe.execute()
            log("Redis host cache 已同步")
        except Exception as e:
            log(f"Redis 同步失败（不影响继续）: {e}", "WARN")

        # 4. write the initialization marker
        # * marker file .agx_init_done.
        #   Old markers are no longer read (install rewrites it every time; leftover old files are harmless).
        marker = Path(workdir) / ".agx_init_done"
        marker.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "network_id": network_id,
                    "host_id": host_id,
                    "mode": "host",
                    "status": "completed",
                },
                indent=2,
            )
        )
        log(f"初始化标记: {marker}")
        log("host 节点初始化完成")

    except Exception as e:
        log(f"host 节点初始化失败: {e}", "ERROR")


def cleanup_start(workdir, mode="core", dry_run=False, no_daemon=False):
    """Clean up temporary files and try to start the daemon

    * Added 2026-10-25, no_daemon (on by default for --host):
       --host targets a "pure worker helper machine" — it deploys only agx_host's config files and
       MCP dependencies, and starts neither daemon nor ws_client. Real scheduling is handled by the
       core node.
       Why the distinction matters: once a daemon starts it subscribes to agx:queue:{host}:*, so if a
       worker node also started a daemon, two processes would compete for the same queue and deliver
       duplicate receipts.
    """
    log_step("Phase 8: 清理与启动")

    if dry_run:
        log("[DRY RUN] 跳过清理")
    else:
        workdir = Path(workdir)
        for item in CLEANUP_ITEMS:
            target = workdir / item
            if target.exists():
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    log(f"已清理: {item}")
                except Exception as e:
                    log(f"清理失败 {item}: {e}", "WARN")

    # * 2026-10-25: --host does not start the daemon by default (no_daemon=True)
    if no_daemon:
        log("host 模式：跳过 daemon 启动（工作节点仅承载 agx_host，调度归 core 节点）")
        _print_host_next_steps(workdir)
        return

    # pass the run mode through to the daemon
    daemon_flag = "--core" if mode == "core" else "--host"

    if IS_WINDOWS:
        bat_name = "start_daemon.bat" if mode == "core" else "start_daemon_host.bat"
        start_bat = Path(workdir) / bat_name
        if start_bat.exists():
            log(f"启动守护进程: {start_bat}")
            if not dry_run:
                subprocess.Popen(
                    [str(start_bat)],
                    cwd=str(workdir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log("守护进程已后台启动")
        else:
            log(f"{bat_name} 不存在，请手动启动", "WARN")
    else:
        # * fixed 2026-11-23 (exposed by the clean-machine drill): the repo has never contained start_daemon.sh,
        #   only daemon.sh (start/stop/restart/status/logs/run).
        #   The old code could never find a start script -> the install printed "installation complete" without starting anything.
        daemon_script = Path(workdir) / "daemon.sh"
        if daemon_script.exists():
            log(f"启动守护进程: bash daemon.sh start {daemon_flag}")
            if not dry_run:
                rc, stdout, stderr = run(
                    f"bash {daemon_script} start {daemon_flag}",
                    cwd=str(workdir),
                    check=False,
                    capture=False,
                )
                if rc == 0:
                    log("守护进程已启动")
                else:
                    log(f"守护进程启动失败(rc={rc}): {(stderr or '')[:200]}", "WARN")
                # read the status back after starting, so the user sees right there whether it actually started
                rc2, out2, _ = run(
                    f"bash {daemon_script} status", cwd=str(workdir), check=False
                )
                if rc2 == 0 and out2:
                    for line in out2.strip().splitlines()[:6]:
                        log(f"  {line}")
                elif rc2 != 0:
                    log("状态回读失败：守护进程可能未启动，请执行 bash daemon.sh status 确认", "WARN")
        else:
            log("daemon.sh 不存在，请手动启动", "WARN")

    # * fixed 2026-11-23 (measured on a clean machine): **not a single step of the install started the MCP gateway**
    #   (31890) — and that is AgentGeeX's only interface. The old flow printed "installation complete" at the end,
    #   `daemon.sh status` was green too, but 31890 was not listening at all and the Agent could not call a single tool.
    #   Nobody noticed historically because old machines still had a hand-made systemd unit running from years ago.
    if not dry_run:
        mcp_script = Path(workdir) / "mcp.sh"
        if mcp_script.exists():
            log("启动 MCP 网关: bash mcp.sh start")
            run(f"bash {mcp_script} start", cwd=str(workdir), check=False, capture=False)
            rc_m, out_m, _ = run(f"bash {mcp_script} status", cwd=str(workdir), check=False)
            if rc_m == 0:
                for line in (out_m or "").strip().splitlines()[:4]:
                    log(f"  {line}")
            else:
                log(
                    "MCP 网关未就绪 —— Agent 将无法调用任何工具。"
                    "排查：bash mcp.sh logs / bash mcp.sh status",
                    "WARN",
                )
            # real liveness probe: not "is the process there" but "can all three mount points initialize"
            rc_c, _out_c, _ = run(f"bash {mcp_script} check", cwd=str(workdir), check=False)
            if rc_c != 0:
                log("MCP 三端点探活失败，请执行 bash mcp.sh check 查看详情", "WARN")
        else:
            log("mcp.sh 不存在，请手动启动 MCP 网关（31890）", "WARN")

    log("\n 安装完成！")
    log(f"工作目录: {workdir}")
    log("")


def _print_host_next_steps(workdir):
    """Post-install guidance for host mode (the daemon is not started)."""
    workdir = Path(workdir)
    log("\n host 节点安装完成！")
    log(f"  工作目录: {workdir}")
    log("")
    log("  本机已就绪：")
    _hrel = agent_home_rel_dir(SYS_HOST, SYS_HOST, resolve_agents_dir_name(workdir))
    log(f"    - {resolve_agents_dir_name(workdir)}/{_hrel}/workspace  （身份与配置）")
    log("    - .venv  （MCP 运行环境）")
    log("")
    log("  后续（按需，非本次安装动作）：")
    log("    1. 在 OpenClaw 的 mcp.servers 里登记 AgentGeeX MCP（见下方说明）")
    log("    2. 如需本机承担调度，再手动执行：bash daemon.sh start --host")
    log("")
    log("  MCP 登记示例（~/.openclaw/openclaw.json → mcp.servers）:")
    log('    "agentgeex": {')
    log('      "command": "uv",')
    log('      "args": ["run", "python", "agx_mcp/mcp_server.py"],')
    log(f'      "cwd": "{workdir}"')
    log("    }")
    log("")


# ═══════════════════════════════════════════════════════════════
# uninstall
# ═══════════════════════════════════════════════════════════════


def delete_agents_via_ws(token, ws_url):
    """Read the Agent list from agx_agent_registry and delete those Agents from the Gateway over WebSocket."""
    print()
    log("正在从数据库获取 Agent 列表...")

    # ── step 1: fetch every Agent ID from the database ──
    from common.mysql.mysqlservice import getSQL2Arr

    try:
        target_ids = getSQL2Arr("SELECT agent_id FROM agx_agent_registry WHERE deploy_status = 'deployed'", "agent_id")
    except Exception as e:
        print(f"ERROR: 数据库查询失败: {e}")
        return

    if not target_ids:
        print("数据库中没有找到 Agent，跳过删除")
        return

    print(f"从数据库获取到 {len(target_ids)} 个 Agent，正在通过 Gateway 删除...")

    # ── step 2: delete them from the Gateway over WebSocket ──
    # clear the system proxy (a local Gateway connection needs none)
    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            del os.environ[k]

    async def _do_delete():
        import websockets

        try:
            ws = await websockets.connect(ws_url, proxy=None, ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"ERROR: 无法连接 Gateway: {e}")
            return

        # handshake
        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": "conn",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 4,
                        "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                        "role": "operator",
                        "scopes": ["operator.read", "operator.write", "operator.admin"],
                        "caps": [],
                        "commands": [],
                        "permissions": {},
                        "auth": {"token": token},
                        "locale": "zh-CN",
                        "userAgent": "agx-uninstaller",
                    },
                }
            )
        )
        resp = await _recv_response(ws)
        if not (resp.get("ok") and resp.get("payload", {}).get("type") == "hello-ok"):
            print(f"ERROR: Gateway 认证失败: {resp}")
            await ws.close()
            return

        # delete one by one (a unique id per request prevents out-of-order Gateway responses)
        deleted, failed = 0, 0
        for idx, aid in enumerate(target_ids):
            req_id = f"del_{idx}"
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": req_id,
                        "method": "agents.delete",
                        "params": {"agentId": aid},
                    }
                )
            )
            resp = await _recv_response(ws, timeout=10)
            if resp.get("ok"):
                print(f"   已删除: {aid}")
                deleted += 1
            else:
                print(f"   删除失败: {aid} — {resp.get('error', '')}")
                failed += 1

        print(f"Agent 删除完成: {deleted} 成功, {failed} 失败")
        await ws.close()

    asyncio.run(_do_delete())


def _stop_daemon_if_running():
    """Stop the running agx daemon process.

    Calls the existing stop_daemon.sh / stop_daemon.bat scripts directly;
    the stop logic is not duplicated inside install.py.
    """
    src_dir = Path(__file__).resolve().parent
    script = src_dir / "stop_daemon.bat" if IS_WINDOWS else src_dir / "stop_daemon.sh"

    if not script.exists():
        log(f"未找到停止脚本: {script}")
        return

    log(f"调用 {script.name} 停止 daemon ...")
    subprocess.run([str(script)], cwd=str(src_dir), capture_output=True, timeout=30)


def _delete_host_agents_via_ws(token, ws_url):
    """Delete host-related Agents from the local Gateway (agx_host + agx_host_* clones)."""
    print()
    log(f"正在从 Gateway 删除 host 类 Agent（{SYS_HOST} 家族）...")

    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            del os.environ[k]

    async def _do_delete():
        import websockets

        try:
            ws = await websockets.connect(ws_url, proxy=None, ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"ERROR: 无法连接 Gateway: {e}")
            return

        await ws.recv()
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": "conn",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 4,
                        "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                        "role": "operator",
                        "scopes": ["operator.read", "operator.write", "operator.admin"],
                        "caps": [],
                        "commands": [],
                        "permissions": {},
                        "auth": {"token": token},
                        "locale": "zh-CN",
                        "userAgent": "agx-uninstall",
                    },
                }
            )
        )
        resp = await _recv_response(ws)
        if not (resp.get("ok") and resp.get("payload", {}).get("type") == "hello-ok"):
            print(f"ERROR: Gateway 认证失败: {resp}")
            await ws.close()
            return

        # list every Agent first
        await ws.send(json.dumps({"type": "req", "id": "list", "method": "agents.list", "params": {}}))
        resp = await _recv_response(ws, timeout=10)
        agents = resp.get("payload", {}).get("agents", [])

        # read the base_id of per_host_unique agents from struct
        struct_path = Path(__file__).resolve().parent / "agx_struct.json"
        unique_base_ids = list(HOST_AGENT_NAMES)  # defaults (new names + old names)
        if struct_path.exists():
            try:
                with open(struct_path) as f:
                    for a in json.load(f).get("agents", []):
                        if a.get("per_host_unique"):
                            unique_base_ids.append(a["agent_id"])
            except Exception:
                pass

        # delete only per_host_unique agents and their clones (a unique id per request prevents out-of-order Gateway responses)
        deleted, failed, skipped = 0, 0, 0
        del_idx = 0
        for a in agents:
            aid = a.get("agentId", a.get("id", ""))
            if not any(aid == bid or aid.startswith(f"{bid}_") for bid in unique_base_ids):
                skipped += 1
                continue
            req_id = f"del_h_{del_idx}"
            del_idx += 1
            await ws.send(
                json.dumps({"type": "req", "id": req_id, "method": "agents.delete", "params": {"agentId": aid}})
            )
            resp = await _recv_response(ws, timeout=10)
            if resp.get("ok"):
                print(f"   已删除: {aid}")
                deleted += 1
            else:
                print(f"   删除失败: {aid} — {resp.get('error', '')}")
                failed += 1

        print(f"Agent 删除完成: {deleted} 成功, {failed} 失败, {skipped} 跳过")
        await ws.close()

    asyncio.run(_do_delete())


def _resolve_config_path(install_src: "Path | None" = None) -> "Path | None":
    """Locate agx_config.json (2026-11-13).

    Background: the config file holds gateway tokens and other credentials, so it belongs to the
    [runtime environment]. The repo no longer ships a real agx_config.json (it would be distributed
    to every customer along with the code, exposing every deployment's tokens). Hence the lookup order:
        1. $AGX_CONFIG_PATH (explicit, highest priority)
        2. ~/.agx/agx_config.json (the runtime directory, the canonical home)
        3. install_src/agx_config.json (legacy layout, not recommended)
    Returns None when everything misses; the caller decides whether to warn or abort.
    """
    env_p = os.environ.get("AGX_CONFIG_PATH", "").strip()
    if env_p and Path(env_p).is_file():
        return Path(env_p)
    runtime = Path(os.path.expanduser("~")) / ".agx" / "agx_config.json"
    if runtime.is_file():
        return runtime
    src = install_src or Path(__file__).resolve().parent
    legacy = Path(src) / "agx_config.json"
    if legacy.is_file():
        return legacy
    return None


def _pick_gateway_cfg(jcfg: dict) -> dict:
    """Get this machine's gateway connection info: gateways first, the singular gateway as a fallback (deprecated).

    2026-11-04: the config is migrating from a singular gateway to a gateways array (one machine may
    run several instances). Prefer the entry whose host_id matches the configured host_id, then any
    usable entry, and only then fall back to the old singular field. Returns {} when neither exists;
    the caller warns on its own.
    """
    gws = jcfg.get("gateways") or []
    if isinstance(gws, list) and gws:
        want = str(jcfg.get("host_id") or "").strip()
        usable = [
            g for g in gws
            if isinstance(g, dict)
            and str(g.get("ws_url") or "").strip()
            and g.get("token")
        ]
        for g in usable:
            if want and str(g.get("host_id") or "").strip() == want:
                return g
        if usable:
            return usable[0]
    return jcfg.get("gateway") or {}


def uninstall_agx(args, mode="core"):
    """Uninstall AgentGeeX.

    core mode: delete every Agent from the Gateway + clean up the database + remove the install directory
    host mode: stop the daemon + delete only the host Agent from the Gateway + remove the install directory
    """
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else resolve_default_workdir()

    banner()
    print(f"  AgentGeeX 卸载程序 ({'核心节点' if mode == 'core' else '工作节点'})")
    print(f"  工作目录: {workdir}")
    if args.dry_run:
        print("   预览模式")
    banner()
    print()

    # load the Gateway config from agx_config.json (read from install.py's directory)
    import json

    jcfg = {}
    config_path = _resolve_config_path()
    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            jcfg = json.load(f)
        gw_cfg = _pick_gateway_cfg(jcfg)
        token = gw_cfg.get("token", "")
        ws_url = gw_cfg.get("ws_url", "ws://127.0.0.1:18789")
    else:
        token = ""
        ws_url = "ws://127.0.0.1:18789"
        log("agx_config.json 不存在，使用默认配置", "WARN")

    if not token:
        log(
            "AGX_GATEWAY_TOKEN 未设置，跳过 Gateway Agent 删除，将仅删除安装目录。请检查 agx_config.json",
            "WARN",
        )

    # ── stop the daemon ──
    if not args.dry_run:
        _stop_daemon_if_running()

    # ── delete Agents from the Gateway ──
    if token and not args.dry_run:
        if mode == "host":
            # host mode: delete only host-type Agents and their clones (agx_host*)
            _delete_host_agents_via_ws(token, ws_url)
        else:
            # core mode: fetch every Agent from the database and delete it
            delete_agents_via_ws(token, ws_url)
    elif args.dry_run:
        if mode == "host":
            print("[DRY RUN] 将从 Gateway 删除 host 类 Agent（agx_host* 家族）及其克隆体")
        else:
            print("[DRY RUN] 将从数据库获取 Agent 列表并通过 Gateway 删除")

    # ── clean up host agent registrations from MySQL (including tasks/groups/Redis) ──
    if mode == "host" and not args.dry_run:
        host_id = jcfg.get("host_id", "")
        try:
            from common.mysql.mysqlservice import getRowsBySQl, updateBySql

            # 1. find every host-type Agent clone of this machine (new names + old name family)
            agents = getRowsBySQl(
                "SELECT agent_id FROM agx_agent_registry WHERE ("
                + " OR ".join(f"agent_id LIKE '{n}%'" for n in HOST_AGENT_NAMES)
                + f") AND host_id = '{host_id}'",
                {"active_status": "1"},
                "",
                "",
            )
            # build env_cfg for cleanup_agent's Redis cleanup
            env_cfg = jcfg.copy()
            env_cfg["AGX_REDIS_HOST"] = jcfg.get("redis", {}).get("host", "127.0.0.1")
            env_cfg["AGX_REDIS_PORT"] = str(jcfg.get("redis", {}).get("port", 6379))
            env_cfg["AGX_REDIS_DB"] = str(jcfg.get("redis", {}).get("db", 0))
            env_cfg["AGX_REDIS_USERNAME"] = jcfg.get("redis", {}).get("username", "default")
            env_cfg["AGX_REDIS_PASSWORD"] = jcfg.get("redis", {}).get("password", "")

            for a in agents or []:
                r = cleanup_agent(a["agent_id"], env_cfg)
                log(
                    f"MySQL: 清理 {a['agent_id']} → tasks={r['deleted']['tasks']} group={r['deleted']['group_members']} registry={r['deleted']['registry']} redis={r['deleted']['redis']}"
                )

            # 2. delete the host record
            updateBySql(f"DELETE FROM agx_host_inventory WHERE host_id = '{host_id}'")
            log("MySQL: 已删除 agx_host_inventory 中的主机记录")
        except Exception as e:
            log(f"MySQL 清理失败（不影响卸载）: {e}", "WARN")

    # ── remove the install directory ──
    if not args.dry_run:
        if workdir.exists():
            if not args.force and not confirm(f"确认删除目录 {workdir} 及其所有内容？", "n"):
                log("卸载取消")
                return
            import shutil

            shutil.rmtree(workdir)
            log(f"已删除: {workdir}")
        else:
            print(f"目录不存在: {workdir}")
    else:
        print(f"[DRY RUN] 将删除: {workdir}")

    banner()
    print("  AgentGeeX 卸载完成")
    banner()


async def _wakeup_via_ws(workdir, env_cfg, agent_id, session_key, wake_msg, mode="core", base_name=""):
    """Create a persistent session over WebSocket sessions.create (bypassing the daemon channel)."""
    import websockets as _ws  # noqa: F811

    ws_url = env_cfg.get("AGX_GATEWAY_WS_URL", "ws://127.0.0.1:18789")
    token = env_cfg.get("AGX_GATEWAY_TOKEN", "")
    if not token:
        log("AGX_GATEWAY_TOKEN 未设置，无法唤醒", "ERROR")
        return False

    ws = await _ws.connect(ws_url, ping_interval=30, ping_timeout=10, proxy=None)
    try:
        await ws.recv()  # hello
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": "conn",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 4,
                        "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                        "role": "operator",
                        "scopes": ["operator.read", "operator.write", "operator.admin"],
                        "caps": [],
                        "commands": [],
                        "permissions": {},
                        "auth": {"token": token},
                        "locale": "zh-CN",
                        "userAgent": "agx-installer",
                    },
                }
            )
        )
        hello = await ws.recv()
        hdata = json.loads(hello)
        if not (hdata.get("ok") and hdata.get("payload", {}).get("type") == "hello-ok"):
            log(f"WS 握手失败: {json.dumps(hdata, ensure_ascii=False)}", "ERROR")
            return False

        # create a persistent agent session (sessions.create, the same path as the daemon)
        # host mode: query the Gateway first to confirm the actual agent_id
        actual_agent_id = agent_id
        if mode == "host":
            await ws.send(json.dumps({"type": "req", "id": "list", "method": "agents.list", "params": {}}))
            list_resp = await _recv_response(ws, timeout=10)
            log(f"  [DEBUG] agents.list: {json.dumps(list_resp, ensure_ascii=False)[:300]}")
            # support several response shapes: payload.agents / payload is the array itself / agents
            agents = (
                list_resp.get("payload", {}).get("agents")
                or (list_resp.get("payload") if isinstance(list_resp.get("payload"), list) else None)
                or list_resp.get("agents", [])
            )
            if not agents:
                log(
                    f"  [DEBUG] agents 为空，原始 payload keys: {list(list_resp.get('payload', {}).keys()) if isinstance(list_resp.get('payload'), dict) else type(list_resp.get('payload')).__name__}",
                    "WARN",
                )
            # * fixed 2026-11-11 F821: _wake_family was never defined before,
            #   so --wakeup --host always raised NameError here. Now it uses the family constant (including old-prefix compatibility).
            _wake_family = _PER_HOST_ID_FAMILIES.get(SYS_HOST, HOST_AGENT_NAMES)
            for a in agents:
                aid = a.get("id") or a.get("agentId", "")
                # prefer the suffixed name (agx_host_XXXX), then the bare name
                if any(aid.startswith(_p + "_") for _p in _wake_family):
                    actual_agent_id = aid
                    session_key = f"agent:{aid}:main"
                    wake_msg = f"唤醒 {aid}，报告你的 host_id 和运行状态"
                    break
            else:
                # no suffix -> try a bare-name match (legacy data compatibility)
                for a in agents:
                    aid = a.get("id") or a.get("agentId", "")
                    if aid == agent_id:
                        actual_agent_id = aid
                        session_key = f"agent:{aid}:main"
                        wake_msg = f"唤醒 {aid}，报告你的 host_id 和运行状态"
                        break
                else:
                    log(f" Gateway 中未找到 {base_name}_* agent", "ERROR")
                    return False

        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": "wake",
                    "method": "sessions.create",
                    "params": {
                        "key": session_key,
                        "agentId": actual_agent_id,
                        "message": wake_msg,
                    },
                }
            )
        )
        resp = await _recv_response(ws, timeout=30)
        ok = resp.get("ok") or resp.get("payload", {}).get("ok")
        if ok:
            log(f" {actual_agent_id} session 已创建 | key={session_key}")
            return True
        else:
            err = resp.get("error") or resp.get("payload", {}).get("error", "unknown")
            log(f" 创建 session 失败 — {err}", "ERROR")
            return False
    finally:
        await ws.close()


def wakeup_agent(args, mode="core"):
    """Wake up an Agent (used after installation).

    core mode: wake agx_core
    host mode: wake every per_host_unique built-in agent
    """
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else resolve_default_workdir()

    if mode == "host":
        # read the base_name of per_host_unique agents from struct
        struct_path = Path(__file__).resolve().parent / "agx_struct.json"
        base_name = SYS_HOST
        if struct_path.exists():
            try:
                with open(struct_path) as f:
                    for a in json.load(f).get("agents", []):  # noqa: F823
                        if a.get("per_host_unique"):
                            base_name = a["agent_name"]
                            break
            except Exception:
                pass
        # use base_name as the agent_id first; _wakeup_via_ws resolves the real id through the Gateway API
        agent_id = base_name
        _wake_family = _PER_HOST_ID_FAMILIES.get(base_name) or (base_name,)
        session_key = f"agent:{base_name}:main"
        wake_msg = f"唤醒 {base_name}，报告你的 host_id 和运行状态"
    else:
        agent_id = SYS_CORE
        session_key = f"agent:{SYS_CORE}:main"
        wake_msg = f"{SYS_CORE}，做好准备"
        base_name = ""

    dry_run = args.dry_run

    # load the Gateway config from agx_config.json
    import json

    config_path = _resolve_config_path()
    gw_cfg = {}
    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            gw_cfg = _pick_gateway_cfg(json.load(f))
    env_cfg = {
        "AGX_GATEWAY_WS_URL": gw_cfg.get("ws_url", "ws://127.0.0.1:18789"),
        "AGX_GATEWAY_TOKEN": gw_cfg.get("token", ""),
    }

    print()
    banner()
    print(f"  AgentGeeX 唤醒程序 ({'核心节点' if mode == 'core' else '工作节点'})")
    print(f"  目标: {agent_id}")
    print(f"  工作目录: {workdir}")
    if dry_run:
        print("   预览模式")
    banner()
    print()

    if dry_run:
        log(f"[DRY RUN] WS sessions.create → {session_key}")
        return

    if not env_cfg["AGX_GATEWAY_TOKEN"]:
        log("AGX_GATEWAY_TOKEN 未设置，请先配置 agx_config.json", "ERROR")
        sys.exit(1)

    # clear the system proxy (a local Gateway connection needs none)
    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            del os.environ[k]

    log(f"正在为 {agent_id} 创建持久化 session...")
    try:
        ok = asyncio.run(_wakeup_via_ws(workdir, env_cfg, agent_id, session_key, wake_msg, mode, base_name))
    except ImportError:
        log("websockets 未安装，无法通过 WS 创建 session。请先 uv sync", "ERROR")
    sys.exit(0 if ok else 1)


def rebuild_cache(env_cfg):
    """Rebuild all Redis cache indexes from MySQL.
    Called after sync_global_struct + init_system have written to MySQL.
    Now uses UsingMysql (DictCursor) to fetch dict rows directly; manual tuple conversion is no longer needed.
    """
    from common.mysql.mysqlconn import UsingMysql

    r = redis_mod.Redis(
        host=env_cfg.get("AGX_REDIS_HOST", "127.0.0.1"),
        port=int(env_cfg.get("AGX_REDIS_PORT", 6379)),
        db=int(env_cfg.get("AGX_REDIS_DB", 0)),
        username=env_cfg.get("AGX_REDIS_USERNAME", "default"),
        password=env_cfg.get("AGX_REDIS_PASSWORD", "") or None,
        decode_responses=True,
    )
    log(
        f"Redis config: host={env_cfg.get('AGX_REDIS_HOST')}, port={env_cfg.get('AGX_REDIS_PORT')}, user={env_cfg.get('AGX_REDIS_USERNAME', 'default')}, pass_len={len(env_cfg.get('AGX_REDIS_PASSWORD', '') or '')}"
    )
    r.ping()

    # clear the old cache first (so repeated installs do not accumulate leftovers)
    #
    # FIX (2026-10-25 production incident): the old `scan_iter("agx:*")` deleted every agx:* key indiscriminately,
    #   wiping two classes of state keys that cannot be rebuilt and causing a flood of bogus notifications:
    #     1. agx:notified:plan_completed:* — the watchdog's dedupe markers (Redis only, no MySQL copy).
    #        Once deleted, all 150 historical completed plans were re-notified as "just completed"
    #        (measured: 1858 notifications vs only 177 markers).
    #     2. agx:idx:plan:* / agx:idx:execution:* — this function does not rebuild those two indexes
    #        (it rebuilds only project/host/agent/task/agent_group), so deleting them robbed the watchdog of its traversal source.
    #   Fix: switch to an allowlist — delete only the entities and indexes this function really rebuilds from MySQL;
    #        state keys (dedupe markers / queues / locks / WS / plan index / execution index) are always preserved.
    _CLEARABLE_PREFIXES = (
        "agx:entity:project:",
        "agx:entity:host:",
        "agx:entity:agent:",
        "agx:entity:task:",
        "agx:entity:agent_group:",
        "agx:entity:agent_group_member:",
        "agx:idx:project:",
        "agx:idx:host:",
        "agx:idx:agent:",
        "agx:idx:task:",
        "agx:idx:agent_group:",
        "agx:idx:agent_group_member:",
    )
    # explicit protection: these keys exist only in Redis, deleting them loses facts, and this function does not rebuild them
    #   * the list matches _PROTECTED_KEY_PREFIXES in agx_mcp/mcp_server.py (same pitfall, same rules in both places)
    _PROTECTED_PREFIXES = (
        "agx:notified",         # plan notification idempotence marker (the main culprit of that incident)
        "agx:notified_fail",    # plan notification failure counter
        "agx:watchdog:",        # watchdog alert/crisis/wake state
        "agx:worker_queues:",   # queue runtime state
        "agx:host_slots:",      # host slot occupancy
        "agx:ws:",              # WS bridge runtime markers + delivery keys
        "agx:dsh:",             # DSH bridge runtime markers
        "agx:queue:",           # message queues (must never be deleted)
        "agx:lock:",            # distributed locks
        "agx:idx:plan:",        # watchdog traversal source (not rebuilt here)
        "agx:idx:execution:",   # execution status index (not rebuilt here)
    )
    deleted = 0
    preserved = 0
    for key in r.scan_iter("agx:*"):
        if key.startswith(_PROTECTED_PREFIXES):
            preserved += 1
            continue
        if not key.startswith(_CLEARABLE_PREFIXES):
            preserved += 1
            continue
        r.delete(key)
        deleted += 1
    log(
        f"Cleared {deleted} stale Redis keys "
        f"(preserved {preserved} state keys: notified/queue/lock/ws/plan-idx/exec-idx)"
    )

    network_id = env_cfg.get("AGX_NETWORK_ID", "lan_001")

    # — Projects —
    with UsingMysql() as um:
        um.cursor.execute("SELECT * FROM agx_project WHERE active_status=1")
        for d in um.cursor.fetchall():
            pid = d["project_id"]
            r.hset(f"agx:entity:project:{pid}", mapping={k: str(v) if v is not None else "" for k, v in d.items()})
            r.zadd("agx:idx:project:all", {pid: int(d.get("pkid", 0))})
            # * FIX (2026-10-25, DH-063): also rebuild the by_network index.
            #   The allowlist deleted agx:idx:project:* but only :all was rebuilt,
            #   while _sync_entity (mcp_server.py:530) writes by_network -> deleting without rebuilding leaves a gap.
            if d.get("network_id"):
                r.sadd(f"agx:idx:project:by_network:{d['network_id']}", pid)

    # — Hosts —
    with UsingMysql() as um:
        um.cursor.execute("SELECT * FROM agx_host_inventory WHERE active_status=1")
        for d in um.cursor.fetchall():
            hid = d["host_id"]
            sd = {k: str(v) if v is not None else "" for k, v in d.items()}
            r.hset(f"agx:entity:host:{hid}", mapping=sd)
            r.zadd("agx:idx:host:all", {hid: int(d.get("pkid", 0))})
            r.sadd(f"agx:idx:host:by_network:{d.get('network_id', network_id)}", hid)
            # * FIX (2026-10-25, DH-063[3]): this used to sadd unconditionally, so offline hosts were counted as online.
            #   Aligned with the MCP side: decide by presence_status, srem when offline.
            if d.get("presence_status") == "online":
                r.sadd("agx:idx:host:online", hid)
            else:
                r.srem("agx:idx:host:online", hid)

    # — Agents —
    with UsingMysql() as um:
        um.cursor.execute("SELECT * FROM agx_agent_registry WHERE active_status=1")
        for d in um.cursor.fetchall():
            aid = d["agent_id"]
            r.hset(f"agx:entity:agent:{aid}", mapping={k: str(v) if v is not None else "" for k, v in d.items()})
            r.zadd("agx:idx:agent:all", {aid: int(d.get("pkid", 0))})
            r.sadd("agx:idx:agent:active", aid)
            # * FIX (2026-10-25, DH-063): also rebuild the by_network index
            #   (_sync_entity, mcp_server.py:530, writes it; previously deleted without rebuilding)
            if d.get("network_id"):
                r.sadd(f"agx:idx:agent:by_network:{d['network_id']}", aid)

    # — Tasks —
    with UsingMysql() as um:
        um.cursor.execute("SELECT * FROM agx_task_base WHERE active_status=1")
        for d in um.cursor.fetchall():
            tid = d["task_id"]
            r.hset(f"agx:entity:task:{tid}", mapping={k: str(v) if v is not None else "" for k, v in d.items()})
            r.zadd("agx:idx:task:all", {tid: int(d.get("pkid", 0))})
            # * FIX (2026-10-25, DH-063): previously only :all was built, so deleting these three left a gap:
            #   · agx:idx:task:active      (written by _sync_entity per active_status)
            #   · agx:idx:task:by_agent:*  (written by _sync_entity per agent_id)
            r.sadd("agx:idx:task:active", tid)
            if d.get("agent_id"):
                r.sadd(f"agx:idx:task:by_agent:{d['agent_id']}", tid)

    # — Agent Groups —
    with UsingMysql() as um:
        um.cursor.execute("SELECT * FROM agx_agent_group WHERE active_status=1")
        for d in um.cursor.fetchall():
            gid = d["group_id"]
            nid = d.get("network_id", network_id)
            r.hset(f"agx:entity:agent_group:{gid}", mapping={k: str(v) if v is not None else "" for k, v in d.items()})
            r.zadd("agx:idx:agent_group:all", {gid: int(d.get("pkid", 0))})
            r.sadd(f"agx:idx:agent_group:by_network:{nid}", gid)
        # members
        um.cursor.execute("SELECT * FROM agx_agent_group_member")
        pkid = 1
        for d in um.cursor.fetchall():
            gid = d["group_id"]
            aid = d["agent_id"]
            key = f"agx:entity:agent_group_member:{gid}:{aid}"
            r.hset(key, mapping={k: str(v) if v is not None else "" for k, v in d.items()})
            r.zadd("agx:idx:agent_group_member:all", {key: pkid})
            r.sadd(f"agx:idx:agent_group_member:by_group:{gid}", aid)
            r.sadd(f"agx:idx:agent_group_member:by_agent:{aid}", gid)
            r.sadd(f"agx:idx:agent_group_member:by_network:{network_id}", key)
            pkid += 1

    log("Redis cache rebuilt from MySQL")


# ═══════════════════════════════════════════════════════════════
# main flow
# ═══════════════════════════════════════════════════════════════


def main():
    ap = argparse.ArgumentParser(
        description="AgentGeeX 跨平台安装脚本",
        epilog=(
            "多实例安装（同一台机器装第二套）必须同时指定三项，缺一项就会串到已有实例：\n"
            "  --workdir         文件装到哪（只决定文件落点，不改变配置的读取位置）\n"
            "  AGX_CONFIG_PATH   读哪个 agx_config.json（默认 ~/.agx/agx_config.json）\n"
            "  AGX_CREDS_ENV     读哪个 agx_creds.env（默认 ~/.agx/agx_creds.env）\n"
            "MCP 端口由 AGX_MCP_PORT 决定（mcp.sh 不读 agx_config.json，默认 31890），\n"
            "第二套请另起端口：./mcp.sh start --port <other-port>\n"
            "\n"
            "例：AGX_CONFIG_PATH=/data/inst2/agx_config.json \\\n"
            "    AGX_CREDS_ENV=/data/inst2/agx_creds.env \\\n"
            "    ./agx.sh --core --workdir /data/inst2/.agx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--core", action="store_true", help="核心节点安装（daemon + watchdog + ws）")
    ap.add_argument("--host", action="store_true", help="工作节点安装（只 agx_host + MCP 环境，不启动 daemon，不初始化数据库）")
    ap.add_argument("--no-daemon", action="store_true", help="安装后不启动 daemon（--host 默认已开启；--core 想手动开局时加）")
    ap.add_argument("--host-only", action="store_true", help=argparse.SUPPRESS)  # backward compatible
    ap.add_argument("--default", action="store_true", help="非交互模式，使用默认值")
    ap.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    ap.add_argument("--workdir", default=None,
                    help="工作目录路径（默认 ~/.agx）。只决定文件装到哪，"
                         "不改变配置/凭据的查找位置（见下方「多实例安装」）")
    ap.add_argument("--force", action="store_true", help="强制覆盖已有安装（等于 overwrite）")
    ap.add_argument("--skip-agent-register", action="store_true",
                    help="跳过 Phase 4 Gateway Agent 注册（WS 协议不匹配时改用 openclaw agents add）")
    ap.add_argument("--skip-db", action="store_true",
                    help="跳过数据库初始化/结构同步/缓存重建（库已存在时用，避免动现有数据）")
    ap.add_argument("--uninstall", action="store_true", help="卸载 AgentGeeX：删除所有已注册 Agent + 安装目录")
    ap.add_argument("--wakeup", action="store_true", help="唤醒 agx_core / agx_host（安装后使用）")
    args = ap.parse_args()

    # mode derivation: --host wins, --host-only is backward compatible (must happen before uninstall/wakeup)
    if args.host or args.host_only:
        mode = "host"
    else:
        mode = "core"  # --core or default both mean core
    dry_run = args.dry_run
    is_default = args.default

    # ── uninstall mode ──
    if args.uninstall:
        uninstall_agx(args, mode=mode)
        return

    # ── wakeup mode ──
    if args.wakeup:
        wakeup_agent(args, mode=mode)
        return

    # determine the install source directory (where install.py lives)
    install_src = Path(__file__).resolve().parent

    # ── Phase 1a: locate the config file ──
    config_file = _resolve_config_path(install_src)
    if config_file is None:
        log("未找到 agx_config.json", "ERROR")
        log(
            "已检查: $AGX_CONFIG_PATH | ~/.agx/agx_config.json | %s/agx_config.json" % install_src
        )
        log(
            "请先把配置放到 ~/.agx/agx_config.json（可参考仓库内 agx_config.json.example）。"
            "账号/密码不写进该文件，另存 ~/.agx/agx_creds.env（chmod 600）。"
        )
        sys.exit(1)

    log_step("Phase 1: 配置确认")

    if not dry_run and not is_default and not confirm("agx_config.json 已配置正确，确认开始安装？", "y"):
        log("用户取消安装，请修改 agx_config.json 后重新运行")
        sys.exit(0)

    # ── Phase 1b: load the config from agx_config.json ──
    import json

    with open(config_file, encoding="utf-8") as f:
        json_cfg = json.load(f)

    mysql_cfg = json_cfg.get("mysql", {})
    redis_cfg = json_cfg.get("redis", {})
    gw_cfg = _pick_gateway_cfg(json_cfg)  # * 2026-11-11: the config moved to the gateways array; the singular form yields no token

    env_cfg = {
        "AGX_NETWORK_ID": json_cfg.get("network_id", "lan_001"),
        "AGX_HOST_ID": json_cfg.get("host_id", ""),
        "AGX_DB_HOST": mysql_cfg.get("host", "127.0.0.1"),
        "AGX_DB_PORT": str(mysql_cfg.get("port", 3306)),
        "AGX_DB_USER": mysql_cfg.get("user", "root"),
        "AGX_DB_PASS": mysql_cfg.get("password", ""),
        "AGX_DB_NAME": mysql_cfg.get("database", "agentgeex"),
        "AGX_REDIS_HOST": redis_cfg.get("host", "127.0.0.1"),
        "AGX_REDIS_PORT": str(redis_cfg.get("port", 6379)),
        "AGX_REDIS_DB": str(redis_cfg.get("db", 0)),
        "AGX_REDIS_PASSWORD": redis_cfg.get("password", ""),
        "AGX_REDIS_USERNAME": redis_cfg.get("username", "default"),
        "AGX_GATEWAY_WS_URL": gw_cfg.get("ws_url", "ws://127.0.0.1:18789"),
        "AGX_GATEWAY_TOKEN": gw_cfg.get("token", ""),
        "AGX_WORKDIR": json_cfg.get("work_dir", ""),
    }

    # ── Phase 1b.5: fill in credentials (fixed 2026-11-23; exposed by the clean-machine drill) ──
    #   Both the install doc and agx_config.json.example require "accounts/passwords do not go into the config file,
    #   put them in ~/.agx/agx_creds.env instead", but the old code read password only from the json -> always an empty string,
    #   so Phase 6 connected as root with an empty password (1698 Access denied) and
    #   Phase 7.5 hit a password-protected Redis with an empty password (AuthenticationError).
    #   Here creds.env is injected into os.environ and then overrides env_cfg — the env file is authoritative.
    _CRED_KEYS = (
        "AGX_DB_HOST", "AGX_DB_PORT", "AGX_DB_USER", "AGX_DB_PASS", "AGX_DB_NAME",
        "AGX_REDIS_HOST", "AGX_REDIS_PORT", "AGX_REDIS_DB",
        "AGX_REDIS_USERNAME", "AGX_REDIS_PASSWORD",
    )
    try:
        from config_loader import _load_creds_env  # the same injection logic; do not build a second copy

        _load_creds_env()
    except Exception as _e:
        log(f"读取 agx_creds.env 失败（将仅用 agx_config.json）: {_e}", "WARN")
    _from_creds = [k for k in _CRED_KEYS if os.environ.get(k)]
    for _k in _from_creds:
        env_cfg[_k] = os.environ[_k]
    if _from_creds:
        log(f"凭据已从 agx_creds.env 载入: {', '.join(_from_creds)}")
    else:
        log("未发现 agx_creds.env 可用项（若数据库需要密码，Phase 6 会失败）", "WARN")

    db_host = env_cfg.get("AGX_DB_HOST", "")
    network_id = env_cfg.get("AGX_NETWORK_ID", "")
    if not db_host or not network_id:
        log("agx_config.json 配置可能不完整，请检查:", "WARN")
        for k, v in [("AGX_DB_HOST", db_host), ("AGX_NETWORK_ID", network_id)]:
            print(f"  {k}={v}")
        if not dry_run and not is_default and not confirm("仍继续安装？", "y"):
            log("安装取消")
            sys.exit(0)

    log("agx_config.json 配置已加载")

    # ── expand ~ paths ──
    wd = env_cfg.get("AGX_WORKDIR", "")
    if wd and "~" in str(wd):
        env_cfg["AGX_WORKDIR"] = str(Path(wd).expanduser())

    # ── Phase 1c: validate host_id ──
    if not env_cfg.get("AGX_HOST_ID"):
        log("agx_config.json 中未配置 host_id，系统必须知道自己的身份。", "ERROR")
        log('请在 agx_config.json 中设置 "host_id" 字段', "ERROR")
        sys.exit(1)

    # ── Phase 1d: generate the unique ids of per_host_unique Agents ──
    per_host_unique = _get_per_host_unique_agents(install_src)
    per_host_unique_ids = {}
    # * 2026-10-25: the suffix is host_id (the host name), not random hex
    _host_suffix = _sanitize_host_suffix(env_cfg.get("AGX_HOST_ID", ""))
    for agent_name, base_id in per_host_unique.items():
        unique_id = f"{base_id}_{_host_suffix}"
        per_host_unique_ids[agent_name] = unique_id
        log(f"{agent_name} 唯一 ID: {unique_id}")
    env_cfg["_per_host_unique_ids"] = per_host_unique_ids
    # legacy compatibility: _host_agent_id points at the first per_host_unique agent
    if per_host_unique_ids:
        first = next(iter(per_host_unique_ids.values()))
        env_cfg["_host_agent_id"] = first
        env_cfg["_host_agent_id_tentative"] = first

    # ── determine the working directory ──
    if args.workdir:
        workdir = Path(args.workdir).expanduser().resolve()
    elif env_cfg.get("AGX_WORKDIR"):
        workdir = Path(env_cfg["AGX_WORKDIR"]).expanduser().resolve()
    else:
        workdir = resolve_default_workdir()

    print("=" * 50)
    print("  AgentGeeX 跨平台安装程序")
    print(f"  模式: {'核心节点（完整安装）' if mode == 'core' else '工作节点（轻量安装）'}")
    print(f"  源目录: {install_src}")
    print(f"  工作目录: {workdir}")
    print(f"  操作系统: {os_platform()}")
    if dry_run:
        print("   预览模式 — 不会实际修改文件")
    print("=" * 50)
    print()

    # ── Phase 2: dependency detection ──
    if not check_deps(mode=mode):
        sys.exit(1)

    # ── Phase 3: working directory ──
    log_step("Phase 3: 工作目录设置")
    log(f"工作目录: {workdir}")

    overwrite = args.force
    dir_nonempty = workdir.exists() and list(workdir.iterdir())

    if dir_nonempty and not overwrite and not is_default:
        print()
        log("目录非空，请选择:", "WARN")
        print("  [o] overwrite — 覆盖安装（清空并重新拷贝）")
        print("  [u] use       — 保留现有文件")
        print("  [q] quit     — 退出")
        choice = read_input("选择", "u").lower()
        if choice.startswith("o"):
            overwrite = True
        elif choice.startswith("q"):
            log("安装取消")
            sys.exit(0)

    # interactive mode: the directory is non-empty and overwrite was not chosen; ask whether to copy files
    copy_files = overwrite  # overwrite mode always copies
    if dir_nonempty and not overwrite and not dry_run and not is_default:
        if confirm(f"拷贝项目文件到 {workdir} ?", "y"):
            copy_files = True
        else:
            log("跳过文件拷贝")

    setup_workdir(install_src, workdir, overwrite=overwrite, copy_files=copy_files, dry_run=dry_run)

    # ── Phase 4: Agent registration ──
    if getattr(args, "skip_agent_register", False):
        log_step("Phase 4: 注册 Agent")
        log("--skip-agent-register：跳过（改用 openclaw agents add）")
    else:
        register_agents(workdir, mode, env_cfg, dry_run=dry_run)

    # ── Phase 5: file sync ──
    sync_files(install_src, workdir, mode, env_cfg, dry_run=dry_run)

    # ── Phase 6: database initialization (core mode only) ──
    if getattr(args, "skip_db", False):
        log_step("Phase 6: 数据库初始化")
        log("--skip-db：跳过数据库初始化（沿用现有库）")
    elif mode == "core":
        init_database(workdir, env_cfg, dry_run=dry_run)
    else:
        log_step("Phase 6: 数据库初始化")
        log("host 模式：跳过数据库初始化（由 core 节点负责）")

    # ── Phase 6.5: struct sync ──
    if getattr(args, "skip_db", False):
        log_step("Phase 6.5: 结构同步")
        log("--skip-db：跳过结构同步（不写 agent/group/task 到 DB）")
    elif mode == "core":
        sync_global_struct(install_src, workdir, env_cfg, dry_run=dry_run)
        # if the DB corrected a per_host_unique agent id, clean up the ghost agent in the Gateway
        per_host_unique_ids = env_cfg.get("_per_host_unique_ids", {})
        tentative = env_cfg.get("_host_agent_id_tentative", "")
        for _name, final_id in per_host_unique_ids.items():
            if tentative and final_id != tentative:
                log(f"清理 Gateway 幽灵 agent: {tentative}")
                _delete_gateway_agent(env_cfg, tentative)
                break
    else:
        log_step("Phase 6.5: 结构同步")
        sync_global_struct(install_src, workdir, env_cfg, dry_run=dry_run, host_only=True)

    # ── Phase 7: local machine initialization ──
    if mode == "core":
        init_system(workdir, env_cfg, dry_run=dry_run)
    else:
        _init_host_only(workdir, env_cfg, dry_run=dry_run)

    # ── Phase 7.5: rebuild the Redis cache ──
    if getattr(args, "skip_db", False):
        log_step("Phase 7.5: Redis 缓存重建")
        log("--skip-db：跳过缓存重建（避免清 agx:notified 等不可重建的状态键）")
    elif not dry_run:
        log_step("Phase 7.5: Redis 缓存重建")
        rebuild_cache(env_cfg)

    # ── Phase 8: cleanup + start ──
    # * 2026-10-25: --host does not start the daemon by default; --core needs an explicit --no-daemon to skip it
    no_daemon = args.no_daemon or (mode == "host")
    cleanup_start(workdir, mode=mode, dry_run=dry_run, no_daemon=no_daemon)


if __name__ == "__main__":
    main()
