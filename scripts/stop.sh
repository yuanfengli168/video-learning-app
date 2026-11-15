#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stop.sh — Stop the Video Learning App (uvicorn on port 8000)
# Usage:  bash scripts/stop.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

PID="$(pgrep -f 'uvicorn app.main' || true)"

if [[ -z "$PID" ]]; then
    warn "No uvicorn process found"
    # Even if pgrep didn't find it, port might still be held by a
    # zombie. Try once more via lsof to be safe.
    if lsof -ti:8000 &>/dev/null; then
        warn "Port 8000 is still occupied though — force-killing holder"
        lsof -ti:8000 | xargs kill -9
        ok "Killed process on port 8000"
    fi
    exit 0
fi

echo "Found uvicorn: PID=$PID"
kill "$PID"
sleep 1

# Confirm it actually died; if not, escalate to SIGKILL
if kill -0 "$PID" 2>/dev/null; then
    warn "Process $PID didn't exit on SIGTERM, sending SIGKILL"
    kill -9 "$PID"
fi

# Final safety net: anything still on port 8000
if lsof -ti:8000 &>/dev/null; then
    warn "Port 8000 still held — killing holder"
    lsof -ti:8000 | xargs kill -9
fi

ok "Server stopped"
