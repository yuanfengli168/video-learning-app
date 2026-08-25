#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stop.sh — Stop the Video Learning App (gunicorn or uvicorn on port 8000)
# Usage:  bash scripts/stop.sh
#
# Day 6: works for both gunicorn (production) and uvicorn (dev) by
# pattern-matching on the process name. Sends SIGTERM first (graceful
# shutdown for in-flight requests) and escalates to SIGKILL if the
# process doesn't exit within 5s.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

# Find any process serving our app — gunicorn (prod) or uvicorn (dev).
# - gunicorn master sets proc_name='video-learning-app', so any process
#   with that name is ours.
# - uvicorn dev mode has 'uvicorn app.main:app' in the cmdline.
# - Plain 'pgrep -f gunicorn' also catches the master + workers, but
#   the master is what we send SIGTERM to (workers follow).
PIDS="$(pgrep -f 'video-learning-app|gunicorn|uvicorn app.main' || true)"

if [[ -z "$PIDS" ]]; then
    warn "No gunicorn/uvicorn process found"
    # Even if pgrep didn't find it, port might still be held by a
    # zombie. Try once more via lsof to be safe.
    if lsof -ti:8000 &>/dev/null; then
        warn "Port 8000 is still occupied though — force-killing holder"
        lsof -ti:8000 | xargs kill -9
        ok "Killed process on port 8000"
    fi
    exit 0
fi

# Send SIGTERM to all matching PIDs (master + workers, or just uvicorn).
# gunicorn master will signal workers to drain; uvicorn just exits.
echo "Found processes: $PIDS"
for PID in $PIDS; do
    kill "$PID" 2>/dev/null || true
done

# Wait up to 5s for graceful shutdown (gunicorn graceful_timeout=30,
# but in practice everything finishes in <2s for in-flight requests).
for i in 1 2 3 4 5; do
    if ! pgrep -f 'video-learning-app|gunicorn|uvicorn app.main' &>/dev/null; then
        ok "Server stopped"
        exit 0
    fi
    sleep 1
done

# Escalate to SIGKILL.
warn "Processes didn't exit gracefully within 5s, sending SIGKILL"
PIDS="$(pgrep -f 'video-learning-app|gunicorn|uvicorn app.main' || true)"
for PID in $PIDS; do
    kill -9 "$PID" 2>/dev/null || true
done

# Final safety net: anything still on port 8000
if lsof -ti:8000 &>/dev/null; then
    warn "Port 8000 still held — killing holder"
    lsof -ti:8000 | xargs kill -9
fi

ok "Server stopped (forced)"
