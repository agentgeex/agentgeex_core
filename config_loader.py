"""
config_loader.py — the unified AgentGeeX configuration loader

Reads every setting from a JSON config file instead of assembling env vars.

Priority:
  1. the JSON file named by the AGX_CONFIG_PATH env var
  2. ../agx_config.json relative to this script's directory
  3. agx_config.json in the current working directory

* Credentials overlay (Lao Feng, 2026-11-13):
  Accounts/passwords no longer live in agx_config.json; they come from env vars.
  After loading the config this module overrides the mysql/redis connection fields
  from the environment; if <work_dir>/agx_creds.env exists it is injected into
  os.environ first (without overwriting existing values).
  -> one change takes effect for every reader (common/mysql, agx_mcp/*, scripts/*).
  Env var names are listed in _ENV_MAP.

Usage:
  from config_loader import config
  redis_host = config.redis.host
  db_password = config.mysql.password
"""

import json
import os
from typing import Any


# ══════════════════════════════════════════════════════════════════════
# credentials env-var mapping table (2026-11-13)
#   Lao Feng decided: accounts/passwords no longer appear in the config file — a secret binds to "who is running",
#   not to "the code/config". When a shared machine changes hands or host, only the env file changes.
# ══════════════════════════════════════════════════════════════════════
_ENV_MAP = {
    "mysql": {
        "host": "AGX_DB_HOST",
        "port": "AGX_DB_PORT",
        "user": "AGX_DB_USER",
        "password": "AGX_DB_PASS",
        "database": "AGX_DB_NAME",
    },
    "redis": {
        "host": "AGX_REDIS_HOST",
        "port": "AGX_REDIS_PORT",
        "db": "AGX_REDIS_DB",
        "username": "AGX_REDIS_USERNAME",
        "password": "AGX_REDIS_PASSWORD",
    },
}

# default location of the credentials file (runtime directory; never in the code repo)
_DEFAULT_CREDS_FILE = os.path.expanduser("~/.agx/agx_creds.env")

def _load_creds_env(path: str = "") -> int:
    """Inject the KEY=VALUE pairs from the credentials file into os.environ (without overwriting).

    Returns the number of entries injected. Returns 0 silently when the file does not
    exist (pure env-var deployment is allowed).
    Shell-compatible format: # comments, export prefixes and paired quotes are supported.
    """
    p = path or os.environ.get("AGX_CREDS_ENV", "") or _DEFAULT_CREDS_FILE
    if not p or not os.path.isfile(p):
        return 0
    injected = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
                    injected += 1
    except OSError:
        return injected
    return injected

def _apply_env_overrides(data: dict) -> dict:
    """Override the connection fields in the config from env vars; types follow the original value (int stays int)."""
    for section, mapping in _ENV_MAP.items():
        sec = data.get(section)
        if not isinstance(sec, dict):
            sec = {}
            data[section] = sec
        for key, env_name in mapping.items():
            val = os.environ.get(env_name)
            if val is None or val == "":
                continue
            old = sec.get(key)
            if isinstance(old, int) and not isinstance(old, bool):
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    continue
            sec[key] = val
    return data


def _warn_if_creds_missing(data: dict) -> None:
    """Emit one clear hint when credentials are missing (never prints any value).

    Why: with an empty mysql.user, pymysql falls back to the system login name and the
    error reads `Access denied for user 'fengxiang'@'localhost'` — which looks like
    "the system user has no privileges" while the real cause is "no credentials configured".
    """
    m = data.get("mysql") or {}
    if m.get("user"):
        return
    import sys as _sys
    _sys.stderr.write(
        "[config_loader] WARNING: MySQL 账号未配置。\n"
        "  配置文件已不再保存账密，请建 ~/.agx/agx_creds.env 并提供：\n"
        "    AGX_DB_USER / AGX_DB_PASS （以及可选的 HOST/PORT/NAME）\n"
        "  或直接导出同名环境变量。\n"
    )


def _find_config_path() -> str:
    """Locate the config file by ascending priority."""
    # 1. env var override
    env_path = os.environ.get("AGX_CONFIG_PATH", "")
    if env_path and os.path.isfile(env_path):
        return env_path

    # * 2026-11-11: the config file is now named agx_config.json.
    #   The old name was retired together with the rename (Lao Feng: old names are not kept).
    #   If an old install directory still has the old name, rename it to agx_config.json before starting.
    _names = ("agx_config.json",)

    # 2a. same directory (config_loader.py sits at the project root)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    same_dir = ""
    for _n in _names:
        _p = os.path.join(script_dir, _n)
        if os.path.isfile(_p):
            return _p
        same_dir = same_dir or _p

    # 2b. parent directory (config_loader.py sits in a subdirectory such as scripts/)
    sibling = ""
    for _n in _names:
        _p = os.path.normpath(os.path.join(script_dir, "..", _n))
        if os.path.isfile(_p):
            return _p
        sibling = sibling or _p

    # 3. current working directory
    cwd = ""
    for _n in _names:
        _p = os.path.join(os.getcwd(), _n)
        if os.path.isfile(_p):
            return _p
        cwd = cwd or _p

    # 4. * 2026-11-13: runtime-directory fallback (~/.agx/agx_config.json)
    #    Configuration belongs to the runtime environment; the repo must not contain agx_config.json (it holds credentials and would ship with the code).
    #    Once the repo copy is deleted, a process started from the repo directory relies on this fallback so it does not fail at startup.
    #    Listed last = it does not change the existing priority, it only fills in when everything above misses.
    runtime_dir = ""
    for _n in _names:
        _p = os.path.join(os.path.expanduser("~"), ".agx", _n)
        if os.path.isfile(_p):
            return _p
        runtime_dir = runtime_dir or _p

    raise FileNotFoundError(
        "找不到 agx_config.json。\n"
        f"已检查: {env_path or '(未设置 AGX_CONFIG_PATH)'}\n"
        f"        {same_dir}\n"
        f"        {sibling}\n"
        f"        {cwd}\n"
        f"        {runtime_dir}\n"
        "请设置环境变量 AGX_CONFIG_PATH 或确保配置文件存在。"
    )


class _Config:
    """Dot-attribute access to config entries, with nesting."""

    # already-warned missing sections (avoid one log line per read)
    _warned_missing: set = set()

    def __init__(self, data: dict):
        for key, value in data.items():
            if isinstance(value, dict):
                setattr(self, key, _Config(value))
            else:
                setattr(self, key, value)

    def __getattr__(self, name: str):
        """A missing config section yields an empty config instead of raising AttributeError.

        * Exposed by the 2026-11-23 clean-machine drill:
          install doc Step 4.2 hands out a **minimal config** (only host_id/network_id/
          workdir/mode/agent_type/mysql/redis/gateways), with no daemon / ws /
          mcp sections. But callers all write `config.daemon.get("poll_interval", 2)` —
          a missing attribute raises AttributeError straight away, so agx_daemon.py
          crashes on import:

              AttributeError: '_Config' object has no attribute 'daemon'

          Result: the install log says "installation complete" all the way through,
          while not a single process was started.

        Semantics: a missing section means every field falls back to the caller's
        .get(default). That is exactly how that code was written (every field carries
        a default), so returning an empty _Config is compatible.
        But it is **not silent** — the first time a given section is missing we print
        one line, so a mistyped section name does not go unnoticed.
        """
        if name.startswith("__") or name.startswith("_"):
            raise AttributeError(name)
        if name not in _Config._warned_missing:
            _Config._warned_missing.add(name)
            import sys as _sys

            print(
                f"[config] 缺少配置段 '{name}'，已按默认值处理"
                f"（若这是笔误，请检查 agx_config.json）",
                file=_sys.stderr,
            )
        return _Config({})

    def get(self, key: str, default: Any = None) -> Any:
        # * must go through __dict__, never getattr:
        #   this class overrides __getattr__ (a missing section returns an empty _Config), so
        #   getattr(key, default) would never yield the default on a missing field, but an empty _Config.
        return self.__dict__.get(key, default)

    def __repr__(self):
        items = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        return f"Config({items})"


_config_instance: _Config | None = None


def load() -> _Config:
    """Load and cache the configuration."""
    global _config_instance
    if _config_instance is None:
        # inject the credentials file first, then read the config, then apply env overrides
        _load_creds_env()
        path = _find_config_path()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _apply_env_overrides(data)
        _warn_if_creds_missing(data)
        _config_instance = _Config(data)
    return _config_instance


def reload(path: str | None = None) -> _Config:
    """Force a reload of the configuration (usable for SIGHUP hot reload)."""
    global _config_instance
    if path:
        os.environ["AGX_CONFIG_PATH"] = path
    _config_instance = None
    return load()


# module-level convenience access (loads on import)
config = load()


# ── gateway entry normalization (* single definition point, 2026-11-17) ────────────
#
# Why this lives in config_loader rather than in each script:
#   the decision "how many connections this host opens, and of what type" is needed by
#   ws_client / daemon / agx_state_relay / agx_dsh_supervisor — four places. Four copies
#   drift sooner or later (it already drifted once: daemon honoured dsh:true entries while
#   ws_client did not, so one config entry meant different things in two components).
#   One normalization, four consumers, one meaning.

AGENT_TYPE_OPENCLAW = "openclaw"
AGENT_TYPE_DSH = "dsh"


def has_dsh_entries() -> bool:
    """Whether the config contains any entry with agent_type=dsh.

    * Why a separate function (2026-11-17):
      After the refactor the daemon unconditionally starts a supervisor; the supervisor
      reads the config itself, finds "no dsh entry" and exits with code 0, then the
      daemon retries 60s later — so machines without DSH (e.g. test_host_mac) waste time
      on "spawn a process -> it exits immediately" every single day
      (measured: 1440 times a day).

      The rule itself still lives in one place only: everything goes through
      gateway_entries(). This function merely lifts "is there a dsh entry" out of the
      entry list so the daemon can decide "start the launcher or not". The rule is not
      duplicated, therefore it cannot drift.
    """
    return any(
        e["agent_type"] == AGENT_TYPE_DSH and not e["skip"]
        for e in gateway_entries()
    )


def gateway_entries() -> list[dict]:
    """Normalize ``config.gateways`` into a uniform shape.

    Config shape (Lao Feng, 2026-11-17: agent_type added to every entry)::

        "gateways": [
          {"host_id": "test_host_srv",  "agent_type": "openclaw",
           "ws_url": "ws://127.0.0.1:18789", "token": "..."},
          {"host_id": "test_host_srv", "agent_type": "dsh",
           "dsh": {"host": "10.0.0.13", "port": 8791}}   # optional override
        ]

    * Inference when agent_type is absent (backward compatible with old configs)::
        · entry has a non-empty ``dsh`` key -> dsh
        · anything else                     -> openclaw

    * Every returned entry contains::
        host_id    string, already stripped
        agent_type "openclaw" | "dsh"
        ws_url     required for openclaw; empty for dsh
        token      may be empty (dsh does not need one yet; will be added later)
        dsh        entry-level DSH override dict (may be an empty dict)
        entry      the raw entry (for callers that need other fields)
        skip       non-empty = this entry is incomplete and the caller should skip it; the value is the reason

    * This function logs nothing and raises nothing: logging is up to the caller's own
      log facility. Skipped entries are kept in the result (with a skip reason) so a
      caller can tell "no such entry in the config" from "configured but malformed".
    """
    raw = config.get("gateways")
    out: list[dict] = []
    if not isinstance(raw, list) or not raw:
        return out

    for gw in raw:
        if not isinstance(gw, dict):
            out.append({
                "host_id": "", "agent_type": "", "ws_url": "", "token": "",
                "dsh": {}, "entry": {}, "skip": "条目不是对象",
            })
            continue

        hid = str(gw.get("host_id") or "").strip()

        # type: explicit wins, inferred when absent
        at = str(gw.get("agent_type") or "").strip().lower()
        if not at:
            at = AGENT_TYPE_DSH if gw.get("dsh") else AGENT_TYPE_OPENCLAW

        ws_url = str(gw.get("ws_url") or "").strip()
        token = str(gw.get("token") or "").strip()

        dsh_cfg = gw.get("dsh")
        dsh = dsh_cfg if isinstance(dsh_cfg, dict) else {}

        skip = ""
        # * an empty host_id does **not** count as skip: ws_client historically allowed it and fell
        #   back to the local HOST_ID; forcing skip would change that. Callers decide whether to fall back or skip.
        if at not in (AGENT_TYPE_OPENCLAW, AGENT_TYPE_DSH):
            skip = f"agent_type 非法: {at!r}（只接受 openclaw / dsh）"
        elif at == AGENT_TYPE_OPENCLAW and not ws_url:
            skip = "openclaw 条目缺 ws_url"

        out.append({
            "host_id": hid, "agent_type": at, "ws_url": ws_url,
            "token": token, "dsh": dsh, "entry": gw, "skip": skip,
        })

    return out

