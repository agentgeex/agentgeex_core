"""BFF config -- read from environment variables + AgentGeeX kernel config.

Reuses the kernel's agx_config.json (MySQL/Redis connections); BFF-specific parameters come from environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

# Allow adding the agx root to sys.path to reuse agx_mcp / config_loader
# This file lives at <agx_root>/webtools/server/app/config.py
AGX_ROOT = Path(
    os.environ.get(
        "AGX_ROOT",
        Path(__file__).resolve().parents[3],  # …/agentgeex/agentgeex
    )
)

# Fallback: if the derived path does not contain agx_mcp, probe upwards level by level
if not (AGX_ROOT / "agx_mcp" / "__init__.py").is_file():
    for _p in Path(__file__).resolve().parents:
        if (_p / "agx_mcp" / "__init__.py").is_file():
            AGX_ROOT = _p
            break


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ── .env loading (2026-10-20 Feng: parameters must be configurable) ───────────────
# Previously only .env.example existed and nobody read it -> edits had no effect. Now it is loaded at import time
# `<server>/.env` (lower priority than real environment variables: existing values are not overwritten, so export can override temporarily).
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"  # webtools/server/.env
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path, override=False)
        return
    except Exception:  # noqa: BLE001 -- without dotenv, fall back to hand parsing; do not blow up the service over an optional dependency
        pass
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv()


# ══════════════════════════════════════════════════════════════════
# Database / cache connection parameters (2026-11-13 Feng: "env first, json as fallback")
#
# Background: on 2026-11-13 credentials moved from `agx_config.json` to `~/.agx/agx_creds.env`
# (the repo-root `config_loader.py` already does this), but the BFF still read the json directly ->
# once the json moved away the BFF fell back to root/empty password and every page returned 500 (Access denied).
#
# Rule (same as the kernel's config_loader, **using the same environment variable names**; do not invent another set):
#   1. inject the credentials file first (without overwriting existing environment variables):
#        the file named by AGX_CREDS_ENV -> ~/.agx/agx_creds.env -> <AGX_ROOT>/mysql_env.sh (old convention, kept for compatibility)
#   2. then read the corresponding section of agx_config.json (the file may be absent; absence is allowed)
#   3. finally override fields with environment variables: AGX_DB_* / AGX_REDIS_*
#
# ★ Why the BFF does not simply `from config_loader import config`:
#   that module **raises FileNotFoundError** when the json is missing (it blows up on import),
#   while the BFF needs "run on env alone even without a json". So the same rules are implemented here without raising.
#   Environment variable names must match the root config_loader._ENV_MAP exactly, or you get "two places each reading their own" again.
# ══════════════════════════════════════════════════════════════════
_ENV_MAP: dict[str, dict[str, str]] = {
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

_CONN_DEFAULTS: dict[str, dict[str, object]] = {
    "mysql": {
        "host": "127.0.0.1", "port": 3306, "user": "root",
        "password": "", "database": "agentgeex", "charset": "utf8mb4",
    },
    "redis": {"host": "127.0.0.1", "port": 6379, "db": 0, "username": "", "password": ""},
}


def _creds_candidates() -> list[Path]:
    """Credential file candidates (by priority). All are runtime files and never enter the repo."""
    out: list[Path] = []
    env_path = os.environ.get("AGX_CREDS_ENV", "").strip()
    if env_path:
        out.append(Path(env_path).expanduser())
    out.append(Path("~/.agx/agx_creds.env").expanduser())
    # Compatible with an early proposed naming (if someone placed a file under the old name it still works; if absent, skip)
    out.append(AGX_ROOT / "mysql_env.sh")
    return out


_creds_loaded = False


def _load_creds_env() -> int:
    """Inject KEY=VALUE into os.environ (**without overwriting existing values**). Returns the number injected."""
    global _creds_loaded  # noqa: PLW0603
    if _creds_loaded:
        return 0
    _creds_loaded = True
    for path in _creds_candidates():
        try:
            if not path.is_file():
                continue
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
        except OSError:
            continue
    return 0


def conn_settings(section: str) -> dict:
    """Return the mysql / redis connection parameters (json as fallback + environment variable overrides)."""
    _load_creds_env()
    cfg: dict = dict(_CONN_DEFAULTS.get(section) or {})
    try:
        cfg_path = AGX_ROOT / "agx_config.json"
        if cfg_path.is_file():
            import json

            with open(cfg_path, encoding="utf-8") as f:
                sec = (json.load(f) or {}).get(section) or {}
            if isinstance(sec, dict):
                cfg.update({k: v for k, v in sec.items() if v not in (None, "")})
    except Exception:  # noqa: BLE001 -- unreadable config must not stop the service from starting
        pass
    for key, env_name in (_ENV_MAP.get(section) or {}).items():
        val = os.environ.get(env_name)
        if val is None or val == "":
            continue
        old = cfg.get(key)
        if isinstance(old, int) and not isinstance(old, bool):
            try:
                val = int(val)  # type: ignore[assignment]
            except (TypeError, ValueError):
                continue
        cfg[key] = val
    return cfg


def mysql_settings() -> dict:
    return conn_settings("mysql")


def redis_settings() -> dict:
    return conn_settings("redis")


# ★ 2026-11-24: where agx_config.json may live, in priority order.
#   Why this list exists: the kernel (install.py / daemon) puts the config in the *install* root
#   (`~/.agx/agx_config.json`), while the BFF is started from the *source tree*. Looking only at
#   `AGX_ROOT/agx_config.json` therefore found nothing on a standard install, so every `_agx_cfg()`
#   call returned its default -- `network_id` became "", realdata fell back to "lan_001", and the
#   console rendered every list as empty while /readyz still said ok. Measured on the clean 118 box.
def agx_config_paths() -> list[Path]:
    """Candidate config paths, highest priority first (never raises)."""
    out: list[Path] = []
    explicit = os.environ.get("AGX_CONFIG_PATH")
    if explicit:
        out.append(Path(explicit).expanduser())
    out.append(AGX_ROOT / "agx_config.json")
    work = os.environ.get("AGX_WORK_DIR") or os.environ.get("AGX_HOME")
    if work:
        out.append(Path(work).expanduser() / "agx_config.json")
    out.append(Path.home() / ".agx" / "agx_config.json")
    return out


def agx_config_path() -> Path | None:
    """The config file actually in use, or None when none exists."""
    for p in agx_config_paths():
        if p.is_file():
            return p
    return None


def _agx_cfg(key: str, default: str) -> str:
    """Read the top-level fields of agx_config.json (defaults when unreadable).

    ★ Used only for "path derivation for display"; it takes part in no permission or write decision --
      when unreadable it at worst shows the path as "unknown" and must never disable a feature.
"""
    try:
        cfg_path = agx_config_path()
        if cfg_path is not None:
            import json

            with open(cfg_path, encoding="utf-8") as f:
                val = (json.load(f) or {}).get(key)
            if val not in (None, ""):
                return str(val)
    except Exception:  # noqa: BLE001
        pass
    return default


def _mcp_url() -> str:
    """MCP endpoint address -- the single source of truth is the mcp section of agx_config.json.

    [2026-11-04 Feng: unify MCP as a shared service]
    The BFF used to start its own `--transport=sse` instance (31888) running the same code as
    OpenClaw's stdio instance, with the two coexisting. It is now unified onto a **single-process shared
    gateway** (PM2-managed, 31890, services distinguished by path), of which the BFF is just one client.

    Priority: BFF_MCP_URL > BFF_MCP_SSE_URL (old name, compatible with existing deployments) > main config mcp.url
              > the default gateway address.
"""
    env = os.environ.get("BFF_MCP_URL") or os.environ.get("BFF_MCP_SSE_URL")
    if env:
        return env
    url = "http://127.0.0.1:31890/agentgeex/mcp"
    try:
        cfg_path = AGX_ROOT / "agx_config.json"
        if cfg_path.is_file():
            import json

            with open(cfg_path, encoding="utf-8") as f:
                mcp_cfg = (json.load(f) or {}).get("mcp") or {}
            url = str(mcp_cfg.get("url") or url)
    except Exception:  # noqa: BLE001
        pass
    return url


def _mcp_transport(url: str) -> str:
    """The transport is derived from the URL shape; there is no separate switch that could be set wrong.

    Ending in `/sse` = old-style SSE (GET /sse to open a long connection + POST /messages);
    anything else    = Streamable HTTP (single path, one POST per call).

    Why derive rather than configure: two fields (url + transport) can contradict each other,
    and they are really two spellings of one fact -- a contradictory config is harder to diagnose than none.
"""
    return "sse" if url.rstrip("/").endswith("/sse") else "streamable-http"


class Settings:
    """BFF runtime configuration."""

    # service
    host: str = _env("BFF_HOST", "0.0.0.0")
    port: int = int(_env("BFF_PORT", "8090"))

    # CORS: frontend dev(5190) / preview(4190); write both localhost and 127.0.0.1
    cors_origins: list[str] = [
        o.strip()
        for o in _env(
            "BFF_CORS_ORIGINS",
            "http://localhost:5190,http://127.0.0.1:5190,"
            "http://localhost:4190,http://127.0.0.1:4190",
        ).split(",")
        if o.strip()
    ]

    # JWT
    jwt_secret: str = _env("BFF_JWT_SECRET", "change-me-in-production")
    jwt_alg: str = _env("BFF_JWT_ALG", "HS256")
    jwt_expire_minutes: int = int(_env("BFF_JWT_EXPIRE_MINUTES", "43200"))  # 30 days by default (Feng's ruling of 2026-10-20)

    # logging
    log_level: str = _env("BFF_LOG_LEVEL", "INFO")

    # [2026-11-01 Feng] "the source path is missing" -- in the DB, agx_agent_registry's
    # agent_root_dir / agent_workspace_dir are NULL for all 90 agents (deployment info only ever lands on
    # the filesystem and is never written back). So the profile page must be able to say **where it really is**:
    # use the DB value when present, otherwise derive by convention and label it "derived".
    # The derivation needs two truths: work_dir (the runtime root) and host_id (which machine this is).
    work_dir: str = _agx_cfg("work_dir", "~/.agx")
    host_id: str = _agx_cfg("host_id", "")
    # ★ 2026-11-24: the tenant/network this install belongs to. realdata used to hard-code
    #   `_NETWORK_ID = "lan_001"`, so any deployment whose network_id differs (e.g. the clean
    #   118 install with `lan_test`) rendered every list as empty -- hosts, agents, tasks all [].
    #   Single source of truth is agx_config.json.
    network_id: str = _agx_cfg("network_id", "")

    # Read-only fetch cap (a hard upper bound on each realdata entity query, preventing full-table dumps; the default is large enough for real volumes)
    # Note: this is a blow-up guard, not business pagination -- business pagination is done by the frontend DataTable component.
    fetch_limit: int = int(_env("BFF_FETCH_LIMIT", "100000"))

    # MCP endpoint (the BFF calls mcp_server tools through this protocol, fully same-origin with MCP)
    # ★ 2026-11-04: no longer "an SSE instance the BFF started", but a client of the shared gateway.
    mcp_url: str = _mcp_url()
    # Inside the class body the name assigned just above can be referenced directly -- do not call _mcp_url() again, avoiding two disk reads yielding two values.
    mcp_transport: str = _mcp_transport(mcp_url)

    @property
    def mcp_sse_url(self) -> str:
        """Kept for the old name (the existing /readyz, /v1/mcp/status and the frontend all read this key).

        The "sse" in the name is no longer accurate (it may now be streamable-http),
        but renaming the key would make old callers silently receive undefined -- better to use mcp_url in new code.
"""
        return self.mcp_url

    # "Open the project directory in Finder" -- this is a system command executed **on the host running the BFF**.
    # By default only loopback requests may trigger it: someone on the LAN clicking once should not pop Finder on your Mac by default.
    # Set BFF_REVEAL_ALLOW_REMOTE=1 explicitly to trigger it from the LAN.
    reveal_allow_remote: bool = _env("BFF_REVEAL_ALLOW_REMOTE", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }

    # ── speech to text (ASR) ───────────────────────────────────────────
    # WS base address of the local ASR service (reasoning_service/audio/audio_service_server.py).
    # Exists only server-side: the frontend only ever knows /v1/audio/transcribe, so changing machine/port needs no frontend rebuild.
    asr_ws_url: str = _env("BFF_ASR_WS_URL", "ws://127.0.0.1:8888/ws/audio/audio2text")
    # Per-recognition timeout (seconds). Warm ~0.9s, cold (first model load) ~10s -> 30s comfortably covers both.
    asr_timeout: float = float(_env("BFF_ASR_TIMEOUT", "30"))
    # Background probe interval (seconds). The service uses short connections with no "long connection to reconnect", so recovery
    # is discovered by periodic probing. 8s is the trade-off: faster is a pointless handshake waste, slower feels sluggish.
    asr_health_interval: float = float(_env("BFF_ASR_HEALTH_INTERVAL", "8"))

    # ── multiple ASR hosts (2026-11-07 Feng: local compute is limited, another server also runs the speech service) ──
    # JSON list: ``[{"id":"local","name":"Local","url":"ws://127.0.0.1:8888/ws/audio/audio2text","note":"..."}]``
    # Not configured -> derive one from asr_ws_url. **The list is a deployment fact (.env); which one is selected is a runtime preference (json file)**,
    # stored separately: changing the list needs a restart, changing the selection should not. See the top of app/service_settings.py.
    asr_hosts_raw: str = _env("BFF_ASR_HOSTS", "")
    # Preference file (stores "which one is selected right now"). Defaults to webtools/server/.runtime/service_settings.json
    service_settings_file: str = _env("BFF_SERVICE_SETTINGS_FILE", "")


settings = Settings()
