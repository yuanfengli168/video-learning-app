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

# Read PIDs into an array (handles multiple uvicorn processes:
# one on :8000 from start.sh and one on :8443 from start-ios.sh).
# `mapfile` / `read -a` handles the multi-line output of `pgrep` correctly.
PIDS=()
while IFS= read -r p; do
    [[ -n "$p" ]] && PIDS+=("$p")
done < <(pgrep -f 'uvicorn app.main' 2>/dev/null || true)

if [[ ${#PIDS[@]} -eq 0 ]]; then
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

echo "Found uvicorn: PIDs=${PIDS[*]}"
for PID in "${PIDS[@]}"; do
    kill "$PID" 2>/dev/null || true
done

# Wait up to 3s for graceful shutdown
for _ in 1 2 3; do
    sleep 1
    STILL_ALIVE=0
    for PID in "${PIDS[@]}"; do
        if kill -0 "$PID" 2>/dev/null; then
            STILL_ALIVE=1
            break
        fi
    done
    [[ $STILL_ALIVE -eq 0 ]] && break
done

# Escalate to SIGKILL for any survivors
for PID in "${PIDS[@]}"; do
    if kill -0 "$PID" 2>/dev/null; then
        warn "Process $PID didn't exit on SIGTERM, sending SIGKILL"
        kill -9 "$PID" 2>/dev/null || true
    fi
done

# Final safety net: anything still on either port
for PORT in 8000 8443; do
    if lsof -ti:$PORT &>/dev/null; then
        warn "Port $PORT still held — killing holder"
        lsof -ti:$PORT | xargs kill -9
    fi
done

ok "Server stopped (both :8000 and :8443)"