"""Session live-state read layer -- a read-only projection of Redis `agx:gw:*` (2026-11-05).

## Why a separate module

Session live state is written to Redis by the collector of each of the 6 OpenClaw instances (daemon-managed,
event-driven). webtools only "reads + displays" and **never writes `agx:gw:*`** (that is the collector's
territory), and never touches `agx:entity:*` / `agx:idx:*` (that is the AgentGeeX cache; corrupting it would
drag the kernel down).

## Data shape (the single authority, from the collector contract)

    agx:gw:session:<session_key>              Hash, 20 fields, TTL 24h
    agx:gw:conflict:<host_id>:<session_key>   isolation zone: same-named sessions across hosts

★ Cross-host duplicate names are where this layer goes wrong most easily:
  every machine has `agent:main:main`, with an identical key string but a different entity.
  The collector does not overwrite someone else's key; it writes into the isolation zone.
  **Querying only the primary key = 188 / 118 will show Mac's data.**

## Performance constraints

2405 keys cannot be scanned per request (the system status page polls every 5s):
    (1) the host list comes from `agx:idx:gw:session:by_host:*` (6 index keys; scan cost negligible)
    (2) the key list comes from `SISMEMBER` index sets, never `SCAN agx:gw:session:*`
    (3) fields are fetched in one pipelined batch `HGETALL` (500 per batch, to avoid an oversized response)
    (4) the whole result is cached in-process for 4 seconds -- a 5s poll hits the cache yet never shows stale data
"""

from __future__ import annotations

import logging
import threading
import time

from . import kernel

log = logging.getLogger("win3.bff.session_runtime")

# Cache duration (seconds). The system status page polls every 5s -> 4s guarantees at most one real read per poll.
_TTL = 4.0

# Status truth values. A new status on the gateway side falls into unknown rather than vanishing.
_STATUSES = ("running", "idle", "failed", "stalled", "unknown")

# Keys per HGETALL batch (2405 rows ≈ 5 batches)
_BATCH = 500

# [2026-11-13 08:23 Feng] "All the session data in system status became zero; there was no data after the upgrade"
# Root cause: a one-character namespace slice bug in the collector `scripts/gateway_state_relay.py` --
#   with the old `DEFAULT_NAMESPACE = "ao:gw:"`, `ns[3:]` yielded exactly `gw:`;
#   after the rename to `agx:gw:` (one character longer) it still sliced `[3:]` -> `:gw:`,
#   so index keys became `agx:idx::gw:session:by_host:xxx` (**double colon**).
#   This module reads `agx:idx:gw:...` as agreed -> not a single host is listed -> total/byStatus/byHost all 0.
#   No data was lost: `agx:gw:session:*` measured 2187 rows intact; only the index key name was wrong.
#
# Handling: the read side accepts both spellings (no data change, no collector change).
#   ★ The collector still needs that one-character fix (`ns[3:]` -> `ns[4:]`), because otherwise only this module is compatible
#     and other consumers still read nothing. It is outside webtools' boundary; reported to Feng for a decision.
_IDX_PREFIXES = ("agx:idx:gw:", "agx:idx::gw:")

# ── DSH live state (the read side added 2026-11-17 when Feng asked "how do you get DSH session state") ────────
#   Write side: the WS subscription in `scripts/agx_dsh_client.py` (`$events` of `/api/remote.mux`),
#              which on `api-session/status|activity|added` writes `agx:dsh:live:{sid}` and
#              registers itself in the `agx:idx:dsh:live` set.
#   Read side: this module. It reads the set and never KEYS-scans (KEYS is O(N) over the whole DB).
#   ★ Why merge into this module instead of a new endpoint: `agx:gw:session:*` and `agx:dsh:live:*`
#     are isomorphic data (both are "what state is some session_key in right now"), and the frontend reads
#     the `runtime` field from realdata.sessions() -- merging in means zero frontend changes.
_DSH_LIVE_IDX = "agx:idx:dsh:live"
_DSH_LIVE_PREFIX = "agx:dsh:live:"

_lock = threading.Lock()
_cache: dict | None = None
_cache_at: float = 0.0


# ══════════════════════════════════════════════════════════════════
# Field parsing
# ══════════════════════════════════════════════════════════════════
def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _runtime_of(h: dict, host_id: str, from_conflict: bool) -> dict:
    """Redis Hash -> a runtime object the frontend can use directly.

    ★ `context_ratio` of 0 means "unknown", not "empty" (contract section 1.4):
      about 1238 archived sessions have `total_tokens = 0`; the gateway has no token data for them at all.
      It is converted to `None` here and the frontend renders "—". Returning an honest 0.0 would make the
      frontend show 0% and anyone reading the chart would think "context is barely used" -- the opposite of
      the truth (the truth is "unknown").
"""
    total = _int(h.get("total_tokens"))
    ctx = _int(h.get("context_tokens"))
    ratio = round(total / ctx, 4) if total > 0 and ctx > 0 else None

    status = (h.get("status") or "unknown").strip().lower()
    if status not in _STATUSES:
        status = "unknown"

    return {
        "status": status,
        "hasActiveRun": status == "running",
        # None = unknown (no token data). The frontend must show "—", never 0%.
        "contextRatio": ratio,
        "totalTokens": total,
        "contextTokens": ctx,
        "lastEventAt": _int(h.get("last_event_at")),
        "lastEventType": h.get("last_event_type") or "",
        "hostId": (h.get("host_id") or host_id or ""),
        "agentId": h.get("agent_id") or "",
        "model": h.get("model") or "",
        "activeRunId": h.get("active_run_id") or "",
        "lastRunId": h.get("last_run_id") or "",
        "errorCount": _int(h.get("error_count")),
        "lastError": h.get("last_error") or "",
        "fromConflict": from_conflict,
    }


def _runtime_of_dsh(h: dict) -> dict:
    """DSH live hash -> a runtime object with the same shape as `_runtime_of`.

    ★ The shape must match: the frontend reads the same batch of fields (status / hasActiveRun / lastEventAt ...).
      One missing field is `undefined` in the frontend, not "no data".
    ★ Token-like fields do not exist on the DSH side (`dsh web` does not expose context usage):
      always 0 / None, and the frontend shows "—". That is "unknown", not "zero usage".
"""
    running = str(h.get("running") or "0").strip() == "1"
    return {
        "status": "running" if running else "idle",
        "hasActiveRun": running,
        "contextRatio": None,
        "totalTokens": 0,
        "contextTokens": 0,
        # the write side already uses milliseconds (same definition as gateway_state_relay's recv_ms); no conversion needed
        "lastEventAt": _int(h.get("last_active_ms")),
        "lastEventType": "dsh:session",
        "hostId": h.get("host_id") or "",
        "agentId": "",
        "model": "",
        "activeRunId": "",
        "lastRunId": "",
        "errorCount": 0,
        "lastError": "",
        "fromConflict": False,
        "source": "dsh",
    }


# ══════════════════════════════════════════════════════════════════
# Reads
# ══════════════════════════════════════════════════════════════════
def _host_list(r) -> list[str]:
    """The host list comes from index-key prefixes (not a scan of data keys): 6 of them, negligible cost.

    ★ Both prefixes are scanned: the collector currently writes the double-colon one (see the `_IDX_PREFIXES` note).
"""
    out: list[str] = []
    for pfx in _IDX_PREFIXES:
        for k in r.scan_iter(match=f"{pfx}session:by_host:*", count=500):
            host = k.rsplit(":", 1)[-1]
            if host:
                out.append(host)
    return sorted(set(out))


def _hgetall_batch(r, keys: list[str]) -> list[dict]:
    """Batch HGETALL, batched with transaction=False (read-only; no need to pay for MULTI)."""
    out: list[dict] = []
    for i in range(0, len(keys), _BATCH):
        chunk = keys[i : i + _BATCH]
        pipe = r.pipeline(transaction=False)
        for k in chunk:
            pipe.hgetall(k)
        out.extend(pipe.execute())
    return out


def _load() -> dict:
    r = kernel.redis_client()
    hosts = _host_list(r)

    # (1) index: each host's primary-key set + isolation-zone set (one pipeline fetch)
    #   ★ Query every prefix and take the union -- after the rename the collector writes into the double-colon one,
    #     and recognising only one means "sessions on all 6 hosts drop to zero".
    pipe = r.pipeline(transaction=False)
    for h in hosts:
        for pfx in _IDX_PREFIXES:
            pipe.smembers(f"{pfx}session:by_host:{h}")
            pipe.smembers(f"{pfx}session:conflict_by_host:{h}")
    res = pipe.execute()

    width = 2 * len(_IDX_PREFIXES)
    by_host_keys: dict[str, list[str]] = {}
    conflict_keys: list[tuple[str, str]] = []
    for i, h in enumerate(hosts):
        base = i * width
        main: set[str] = set()
        conf: set[str] = set()
        for j in range(len(_IDX_PREFIXES)):
            main |= set(res[base + 2 * j] or ())
            conf |= set(res[base + 2 * j + 1] or ())
        by_host_keys[h] = sorted(main)
        conflict_keys.extend((h, k) for k in sorted(conf))

    # (2) primary keys: key -> host (a key appears only in one host's primary-key zone; only isolation-zone keys are duplicates)
    key_host: dict[str, str] = {}
    for h in hosts:
        for k in by_host_keys[h]:
            key_host.setdefault(k, h)

    main_keys = list(key_host)
    main_vals = _hgetall_batch(r, [f"agx:gw:session:{k}" for k in main_keys])
    conflict_vals = _hgetall_batch(r, [f"agx:gw:conflict:{h}:{k}" for h, k in conflict_keys])

    # (3) assemble
    index: dict[str, dict] = {}
    for k, h in zip(main_keys, main_vals, strict=False):
        if not h:  # TTL expiry (key gone) -> treat as absent; do not return an empty shell
            continue
        index[k] = _runtime_of(h, key_host[k], False)

    conflicts: dict[str, dict[str, dict]] = {}
    for (h, k), v in zip(conflict_keys, conflict_vals, strict=False):
        if not v:
            continue
        conflicts.setdefault(k, {})[h] = _runtime_of(v, h, True)

    # (4) which hosts the same session_key appears on (cross-host duplicates)
    hosts_of: dict[str, list[str]] = {}
    for h in hosts:
        for k in by_host_keys[h]:
            hosts_of.setdefault(k, []).append(h)
    for k, per_host in conflicts.items():
        bucket = hosts_of.setdefault(k, [])
        for h in per_host:
            if h not in bucket:
                bucket.append(h)

    # (5) merge DSH live state into the same index (2026-11-17)
    #    DSH's `{agent_id}-{name}` has already been reverse-mapped by the write side into `agent:{agent_id}:{name}` and stored
    #    in the hash's `session_key` field -- the read side must not re-implement the mapping rule (two rules always drift).
    #    setdefault: if the same key also exists on the OpenClaw side (theoretically impossible), the main platform wins.
    dsh_count = 0
    try:
        sids = sorted(r.smembers(_DSH_LIVE_IDX) or ())
        if sids:
            vals = _hgetall_batch(r, [f"{_DSH_LIVE_PREFIX}{sid}" for sid in sids])
            for sid, h in zip(sids, vals, strict=False):
                if not h:                      # TTL expiry (key gone) -> skip
                    continue
                sk = (h.get("session_key") or "").strip()
                if not sk or sk in index:      # no mapping / already held by the main platform -> do not overwrite
                    continue
                index[sk] = _runtime_of_dsh(h)
                hosts_of.setdefault(sk, []).append(h.get("host_id") or "")
                dsh_count += 1
    except Exception as e:  # noqa: BLE001
        # DSH is an incremental capability: a read failure must never take down the whole session list
        log.warning("session_runtime dsh live load failed: %s", e)

    return {
        "at": time.time(),
        "hosts": hosts,
        "index": index,
        "conflicts": conflicts,
        "hostsOf": hosts_of,
        "dshCount": dsh_count,
        "counts": {h: len(by_host_keys[h]) for h in hosts},
        "conflictCounts": {h: len([1 for (hh, _) in conflict_keys if hh == h]) for h in hosts},
    }


def load(force: bool = False) -> dict:
    """Take the cache (4s). force=True forces a re-read (for debugging/acceptance)."""
    global _cache, _cache_at
    now = time.time()
    with _lock:
        if not force and _cache is not None and (now - _cache_at) < _TTL:
            return _cache
    try:
        data = _load()
    except Exception as e:  # noqa: BLE001
        # Redis being unavailable must not kill the board: return an empty projection so the frontend shows "—" everywhere,
        # with sources marking backend=unavailable, so people know it is "cannot read" not "nothing there".
        log.warning("session_runtime load failed: %s", e)
        data = {
            "at": now, "hosts": [], "index": {}, "conflicts": {}, "hostsOf": {},
            "counts": {}, "conflictCounts": {}, "error": str(e),
        }
    with _lock:
        _cache = data
        _cache_at = now
    return data


# ══════════════════════════════════════════════════════════════════
# Queries
# ══════════════════════════════════════════════════════════════════
def lookup(session_key: str, host_hint: str = "") -> dict | None:
    """Fetch the runtime for a session_key.

    `host_hint` = which host this row "should belong to" (the host_id of the session's owning agent).
    Contract section 1.2 read order:
        (1) present in the target host's isolation zone -> use it (for duplicates this is that machine's real data)
        (2) primary key hit with a matching host -> use it
        (3) primary key hit with a different host -> still return it (better than nothing), letting
            `fromConflict` / `hostId` explain the source
    Not found in Redis -> None, and the frontend shows "—".
"""
    data = load()
    key = session_key or ""
    if not key:
        return None

    if host_hint:
        per_host = data["conflicts"].get(key) or {}
        if host_hint in per_host:
            return per_host[host_hint]

    rt = data["index"].get(key)
    if rt is not None:
        return rt
    # Not in the primary key but in the isolation zone (e.g. the collector on the primary machine is not running) -> take the most recent event
    per_host = data["conflicts"].get(key) or {}
    if per_host:
        return max(per_host.values(), key=lambda x: x["lastEventAt"])
    return None


def hosts_of(session_key: str) -> list[str]:
    """Which hosts this session_key appears on (>1 = cross-host duplicate)."""
    return sorted(load()["hostsOf"].get(session_key or "") or [])


# ══════════════════════════════════════════════════════════════════
# Aggregation (the "sessions" card on the system status page)
# ══════════════════════════════════════════════════════════════════
_BUCKETS = (
    ("lt50", "<50%", lambda v: v is not None and v < 0.5),
    ("b50_80", "50–80%", lambda v: v is not None and 0.5 <= v < 0.8),
    ("b80_90", "80–90%", lambda v: v is not None and 0.8 <= v < 0.9),
    ("b90_100", "90–100%", lambda v: v is not None and 0.9 <= v < 1.0),
    ("gte100", "≥100%", lambda v: v is not None and v >= 1.0),
    ("unknown", "未知", lambda v: v is None),
)


def stats() -> dict:
    """Session live-state summary. Definitions are stated explicitly to avoid "the numbers do not add up" arguments."""
    data = load()
    index: dict[str, dict] = data["index"]
    conflicts: dict[str, dict[str, dict]] = data["conflicts"]
    hosts: list[str] = data["hosts"]

    # ★ Entity count: primary-key zone + isolation zone. The isolation zone is not a "duplicate" -- the same session_key
    #   on 188 and 118 are two different entities (independent agent processes and contexts).
    #   So total = 2405 + 2 = 2407 while uniqueKeys = 2405 is reported separately; both are given
    #   so nobody compares total against "deduped session count" and finds a mismatch.
    by_status = dict.fromkeys(_STATUSES, 0)
    by_host: list[dict] = []
    buckets = dict.fromkeys([b[0] for b in _BUCKETS], 0)

    all_rows: list[dict] = list(index.values())
    for per_host in conflicts.values():
        all_rows.extend(per_host.values())

    for rt in all_rows:
        by_status[rt["status"]] = by_status.get(rt["status"], 0) + 1
        ratio = rt["contextRatio"]
        for key, _label, test in _BUCKETS:
            if test(ratio):
                buckets[key] += 1
                break

    for h in hosts:
        mine = [rt for rt in all_rows if rt["hostId"] == h]
        row = {"hostId": h, "total": len(mine)}
        for st in _STATUSES:
            row[st] = sum(1 for rt in mine if rt["status"] == st)
        by_host.append(row)

    top = [
        {
            "sessionKey": k,
            "hostId": rt["hostId"],
            "agentId": rt["agentId"],
            "contextRatio": rt["contextRatio"],
            "status": rt["status"],
        }
        for k, rt in _top_pairs(index, conflicts)
    ]

    return {
        "total": len(all_rows),
        "uniqueKeys": len(set(index) | set(conflicts)),
        "conflicts": sum(len(v) for v in conflicts.values()),
        "byStatus": by_status,
        "byHost": by_host,
        "contextBuckets": [
            {"key": key, "label": label, "count": buckets[key]} for key, label, _t in _BUCKETS
        ],
        "topRisk": _attach_names(top),
        "hosts": hosts,
        # the 5s poll uses this to tell "it really refreshed"
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["at"])),
        "cacheSeconds": _TTL,
        "backend": "redis" if not data.get("error") else "unavailable",
        "error": data.get("error", ""),
    }


def _top_pairs(index: dict, conflicts: dict):
    pairs = [(k, rt) for k, rt in index.items() if rt["contextRatio"] is not None]
    for k, per_host in conflicts.items():
        for rt in per_host.values():
            if rt["contextRatio"] is not None:
                pairs.append((k, rt))
    pairs.sort(key=lambda p: p[1]["contextRatio"], reverse=True)
    return pairs[:5]


def _attach_names(top: list[dict]) -> list[dict]:
    """Fill in the "session name" for the Top 5. Redis has no names; they come from MySQL agx_session (at most 5 rows, negligible cost)."""
    keys = [t["sessionKey"] for t in top if t.get("sessionKey")]
    if not keys:
        return top
    try:
        ph = ",".join(["%s"] * len(keys))
        rows = kernel.query(
            f"SELECT session_id, session_name FROM agx_session WHERE session_id IN ({ph})",
            tuple(keys),
        )
        names = {r["session_id"]: (r.get("session_name") or "") for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("session_runtime names lookup failed: %s", e)
        names = {}
    for t in top:
        t["sessionName"] = names.get(t["sessionKey"], "") or t["sessionKey"]
    return top
