#!/usr/bin/env bash
# ============================================================================
# webtools.sh — compatibility entry (now a retired shell; the real implementation is webtools/start.sh)
#
# History: this script was the launcher of the Streamlit era and ran a Streamlit page directory
#          that no longer exists. The console today is React(Vite) + FastAPI(BFF) + MCP shared
#          gateway — a three-layer stack orchestrated by webtools/start.sh.
#
# Why it is kept: `./webtools.sh start` is old muscle memory; deleting it outright would look like breakage.
# Behaviour: forwards every argument as-is to webtools/start.sh.
#
#    ./webtools.sh start | stop | status | restart | logs | check
#
# ============================================================================
set -euo pipefail

AGX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL="$AGX_ROOT/webtools/start.sh"

if [ ! -x "$REAL" ]; then
    echo "❌ 找不到可执行的 $REAL" >&2
    exit 1
fi

exec "$REAL" "$@"
