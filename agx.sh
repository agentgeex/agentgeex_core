#!/bin/bash
# agx.sh — AgentGeeX unified entry point (install / uninstall / wakeup)
#
# Install:
#   ./agx.sh                  # core node (daemon + watchdog + ws)
#   ./agx.sh --core           # same as above, explicit
#   ./agx.sh --host           # worker node (daemon + ws, lightweight install)
#   ./agx.sh --default        # non-interactive, all defaults
#   ./agx.sh --dry-run        # preview mode
#   ./agx.sh --workdir DIR    # custom working directory
#   ./agx.sh --force          # force overwrite
#
# Uninstall:
#   ./agx.sh --uninstall      # full uninstall
#
# Wakeup:
#   ./agx.sh --wakeup         # wake up agx_core

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

exec uv run python3 install.py "$@"
