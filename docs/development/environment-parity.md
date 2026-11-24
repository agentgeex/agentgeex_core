# Environment Parity: venv Dependencies Must Come From uv.lock

> Established: 2026-11-04
> Triggering incident: 10.0.0.13's Python dependencies drifted from `uv.lock` by one fastmcp major version and it ran silently for half a day

---

## Iron Rule

> **Creating a venv must use `uv sync` (through the lock). Installing project dependencies with `uv pip install` is forbidden.**

`uv pip install` **re-resolves dependencies and installs the newest version**, bypassing `uv.lock`. Only `uv sync` installs according to the lock.

The repository contains `uv.lock` (333 KB, already committed) — that is the source of truth for versions. The `>=` in `pyproject.toml` only declares a lower bound, **it is not a version guarantee** — only the lock is a guarantee.

---

## Why This Rule Exists (the Real Cost)

Measured on all three machines on 2026-11-04:

```
            fastmcp  mcp      python    agx_register_host desc_len
Mac         3.2.4    1.27.0   3.12.13   2052 bytes
10.0.0.12   3.2.4    1.27.0   3.14.4    2052 bytes
10.0.0.13   4.1.3    2.3.0    3.12.13   1991 bytes   ← drifted from the lock
```

`uv.lock` pins `fastmcp 3.2.4` / `mcp 1.27.0`. 10.0.0.13 was not "environment drift" — it was **installed without going through the lock**.

### Symptom: No Error At All

10.0.0.13 ran fine and every MCP tool worked. The only visible difference: **fastmcp 4.x strips the `Returns:` section of docstrings**, so the `agx_register_host` tool description was 61 bytes shorter.

And that is only the visible part. The differences between 4.x and 3.x also cover tool schema generation, argument parsing, error response format, and concurrency/timeout behavior — **none of which raise an error; they just turn into "the same instruction behaves differently on 10.0.0.13" one day**.

**Code parity ≠ runtime parity.** The md5 of `mcp_server.py` was byte-identical across the three machines, yet behavior differed.

---

## How To Check (Three-Machine Consistency Verification)

### 1. Reconcile Dependency Versions

```bash
# Run on each machine (run directly on Mac; use ssh for 10.0.0.12/10.0.0.13)
cd <agx repo> && .venv/bin/python -c "
import sys, importlib.metadata as md
def v(n):
    try: return md.version(n)
    except: return '-'
print('python  :', sys.version.split()[0])
for p in ('fastmcp','mcp','fastapi','uvicorn','httpx'):
    print('%-8s: %s' % (p, v(p)))
"
```

### 2. Does It Match the Lock (the most authoritative step)

```bash
cd <agx repo> && ~/.local/bin/uv sync --dry-run 2>&1 | tail -4
```

Criterion: **`Would make no changes` must appear.**

Any other output (`Would install N packages` / `Would uninstall N packages`) means that machine has drifted from the lock and needs fixing.

### 3. Compare Version by Version Against the Lock (optional, stricter)

```bash
cd <agx repo>
grep -A2 -E '^name = "(fastmcp|mcp)"$' uv.lock
```

---

## How To Fix (a machine that has drifted)

**The order must not be scrambled**: dry-run to see the list first → analyze → stop the service → sync → start the service → verify actual output.

### Step 1: dry-run, and read every difference

```bash
cd <agx repo> && ~/.local/bin/uv sync --dry-run
```

### Step 2: Analyze the differences and **confirm you are not deleting a package "someone installed by hand that is not in the lock"**

Split the output into `-` (uninstall) and `+` (install) and count them, paying attention to the ones that are **removed outright** (removed with nothing replacing them).

The actual result of the 2026-11-04 run:

```
removed outright (6): fastmcp-slim / httpcore2 / httpx2 / mcp-types / truststore / tzdata
             -> all new packages that fastmcp 4.x split out; normal
added (12):   httpx / httpcore / httpx-sse / gitpython / gitdb / smmap
             docutils / blinker / toml / tenacity / importlib-metadata / zipp
             -> all classic 3.x dependencies; normal
downgraded (71): all reconciled to the lock
```

**Criterion**: every removed package must be explainable within the "version change chain". If a package unrelated to the project dependencies shows up (say, a debug tool someone installed for themselves), **stop and ask first**, because `uv sync` will silently wipe it.

### Step 3: Stop the Service → sync → Start the Service

```bash
systemctl --user stop agx-mcp-gateway
cd <agx repo> && ~/.local/bin/uv sync
systemctl --user start agx-mcp-gateway
```

**Why stop first**: a running process that imports a new module mid-sync may load a mixture of old and new packages. Stopping it is the cleanest option.

**No impact on openclaw instances**: `agx-mcp-gateway` and `openclaw-gateway` are two independent processes. Stopping the MCP only makes tool calls unavailable; the agent runtime is unaffected (measured on 2026-11-04: none of the three instances on 10.0.0.13 restarted).

### Step 4: Verify — **do not look at version numbers, look at actual output**

Matching version numbers does not mean matching behavior. The real criterion is the tool description:

```bash
# Send MCP initialize + tools/list and capture the description byte count
# The agx_register_host description must be byte-identical across all three machines
```

```bash
# Capture script essentials:
#   1) POST /agentgeex/mcp  method=initialize  → get the Mcp-Session-Id response header
#   2) POST notifications/initialized
#   3) POST method=tools/list → filter by name from result.tools
#   The Accept header must contain application/json, text/event-stream
```

After the 2026-11-04 fix: the `agx_register_host` description on all three machines was **2052 bytes, and the diff was empty**.

---

## Pitfall List

### `uv` lives in `~/.local/bin`, which a non-interactive ssh cannot find

```
Mac          /Users/<user>/.local/bin/uv    0.10.12
10.0.0.12    /home/<user>/.local/bin/uv     0.11.7
10.0.0.13    /home/owner/.local/bin/uv      0.11.7
```

A plain ssh command's PATH **does not include** `~/.local/bin`, so `uv --version` reports `command not found`.

**This does not mean it is not installed.** How to tell:

```bash
ssh host 'bash -lc "command -v uv"'          # a login shell can find it
ssh host 'cat <repo>/.venv/pyvenv.cfg'       # contains uv = 0.11.7
```

**Conclusion**: to call uv remotely, use `bash -lc`, or write the full path `~/.local/bin/uv`.

### `python3 -V` does not reflect the venv's Python

10.0.0.12's system `python3` is 3.8.10, but the venv is 3.14.4 (a standalone interpreter installed by uv). **Always check `.venv/bin/python -V`**, or read `version_info` in `.venv/pyvenv.cfg`.

### A different Python version ≠ different dependency versions

`uv.lock` is a universal resolution and contains `resolution-markers` branches:

```toml
resolution-markers = [
    "python_full_version >= '3.14' and sys_platform != 'emscripten' and ...",
    "python_full_version < '3.14' and ...",
]
```

10.0.0.12 runs 3.14.4 while the other two run 3.12.13, and **the measured installed package versions were exactly the same**. So a minor Python version mismatch is not a problem in itself — as long as `uv sync --dry-run` returns `Would make no changes` on every machine.

### `uv sync` prunes by default

Packages that are not in the lock are deleted. That is a good property ("a clean environment"), but it is also the one risk that must be confirmed up front. See "Step 2".

---

## Related

- [mcp-gateway.md](../concepts/mcp-gateway.md) — 31890 gateway architecture
- [host-platforms.md](../concepts/host-platforms.md) — host multi-instance and the `platforms` field
- [repo-layout-and-git.md](repo-layout-and-git.md) — where each environment lands on disk, and how to keep the machines identical
