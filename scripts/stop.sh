#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stop.sh — Stop the Video Learning App (uvicorn on port 8000 and 8443)
# Usage:  bash scripts/stop.sh
#
# Stops BOTH servers (the :8000 Mac web app server started by start.sh
# AND the :8443 iOS HTTPS server started by start-ios.sh). Both share
# the same FastAPI process, so killing the process frees both ports.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

PID="$(pgrep -f 'uvicorn app.main' || true)"

if [[ -z "$PID" ]]; then
    warn "No uvicorn process found"
    # Even if pgrep didn't find it, ports might still be held by a
    # zombie. Try once more via lsof to be safe.
    for PORT in 8000 8443; do
        if lsof -ti:$PORT &>/dev/null; then
            warn "Port $PORT is still occupied though — force-killing holder"
            lsof -ti:$PORT | xargs kill -9
            ok "Killed process on port $PORT"
        fi
    done
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

# Final safety net: anything still on either port
for PORT in 8000 8443; do
    if lsof -ti:$PORT &>/dev/null; then
        warn "Port $PORT still held — killing holder"
        lsof -ti:$PORT | xargs kill -9
    fi
done

ok "Server stopped (both :8000 and :8443)"