"""Service runtime settings -- "what is available" and "which one is chosen right now" are stored separately.

## Why two places (the most important point in this whole thing)

| Stored where | Holds | Who changes it | Restart needed? |
|---|---|---|---|
| `.env` (`BFF_ASR_HOSTS`) | which speech-service addresses this deployment **may choose from** | ops/deployment | yes |
| `service_settings.json` | which one is **chosen right now** | a user click | no |

Putting "the choice" in `.env` -> every click means editing a file and restarting; that is not a setting,
that is editing config. Putting "the list" in json -> changing machine means editing a runtime file inside the
program directory; that is chaos.

In one sentence: **the list is a fact (known at deployment); the choice is a preference (people change it any time).**

## Fault tolerance stance

When the list is misconfigured (bad JSON / missing url / duplicate id): **no exception, no failed startup**;
degrade to "only one default host" and log one warning.
Reason: speech-to-text is an **auxiliary feature**, and a misconfiguration there must not stop the whole BFF --
that would magnify a small config slip into a site-wide outage.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger("win3.bff.service_settings")

_DEFAULT_ID = "default"
_DEFAULT_NAME = "默认"


# ── host list (from .env, read-only) ──────────────────────────────


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (text or "").strip().lower()).strip("-")
    return s or fallback


def _normalize_one(raw: Any, idx: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    hid = str(raw.get("id") or "").strip() or _slug(str(raw.get("name") or ""), f"host{idx + 1}")
    return {
        "id": hid,
        "name": str(raw.get("name") or "").strip() or hid,
        "url": url.rstrip("/"),
        "note": str(raw.get("note") or "").strip(),
    }


def hosts() -> list[dict]:
    """Selectable host list. There is always at least one (a misconfiguration falls back to the default one)."""
    raw = (settings.asr_hosts_raw or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("[settings] BFF_ASR_HOSTS 不是合法 JSON（%s），退回单机默认", e)
            parsed = None
        if isinstance(parsed, list):
            out: list[dict] = []
            seen: set[str] = set()
            for i, item in enumerate(parsed):
                h = _normalize_one(item, i)
                if not h:
                    continue
                # a duplicate id makes "selected" lose its referent; from the second one a suffix is appended automatically
                base, n = h["id"], 2
                while h["id"] in seen:
                    h["id"] = f"{base}-{n}"
                    n += 1
                seen.add(h["id"])
                out.append(h)
            if out:
                return out
            log.warning("[settings] BFF_ASR_HOSTS 里没有可用的条目（都缺 url？），退回单机默认")

    return [{
        "id": _DEFAULT_ID,
        "name": _DEFAULT_NAME,
        "url": settings.asr_ws_url.rstrip("/"),
        "note": "来自 BFF_ASR_WS_URL",
    }]


def get(host_id: str) -> dict | None:
    for h in hosts():
        if h["id"] == host_id:
            return h
    return None


# ── preference (local json, writable) ──────────────────────────────


def _path() -> Path:
    p = (settings.service_settings_file or "").strip()
    if p:
        return Path(p).expanduser()
    # webtools/server/.runtime/ -- a runtime directory, not in the repo (see webtools/server/.gitignore)
    return Path(__file__).resolve().parent.parent / ".runtime" / "service_settings.json"


def _read_all() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001 -- a corrupt file must not stop the service from starting
        log.warning("[settings] 读 %s 失败（忽略，用默认）：%s", p, e)
        return {}


def _write_all(data: dict) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)  # atomic replace: a power loss mid-write never leaves half a bad json
    except Exception as e:  # noqa: BLE001
        log.warning("[settings] 写 %s 失败：%s", p, e)


def active_id() -> str:
    """The currently selected host id. If the stored value is no longer valid (that host was removed from .env) -> back to the first one."""
    hs = hosts()
    saved = str(_read_all().get("asr_host_id") or "").strip()
    if saved and any(h["id"] == saved for h in hs):
        return saved
    return hs[0]["id"]


def active() -> dict:
    return get(active_id()) or hosts()[0]


def set_active(host_id: str) -> dict:
    h = get(host_id)
    if not h:
        raise ValueError(f"未知的语音主机：{host_id}")
    data = _read_all()
    data["asr_host_id"] = h["id"]
    _write_all(data)
    log.info("[settings] 语音主机切到 → %s (%s)", h["id"], h["url"])
    return h
