#!/usr/bin/env bash
# Win3 BFF start script - reuses the repo-root .venv (fastapi/uvicorn already installed)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGX_ROOT="$(cd "$HERE/../.." && pwd)"
PY="$AGX_ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "找不到 venv python: $PY" >&2
  exit 1
fi

cd "$HERE"
export AGX_ROOT
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "${BFF_PORT:-8090}" --reload
