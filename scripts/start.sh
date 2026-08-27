#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Start the Video Learning App (Day 6, production-ready)
# Usage:
#   bash scripts/start.sh                  # production (gunicorn, default)
#   SERVER=uvicorn bash scripts/start.sh    # dev (uvicorn --reload)
#
# Day 6: switched from raw `uvicorn --reload` to gunicorn. Why:
#   - gunicorn manages 4 workers × 2 threads (8 concurrent requests)
#   - auto-restarts crashed workers (uvicorn doesn't)
#   - graceful shutdown on SIGTERM (matches Cloudflare Tunnel)
#   - unified access log via stderr → logs/server.log
#   - PID file at /tmp/gunicorn.pid (for stop.sh)
#
# SERVER env var lets us keep the dev workflow (`SERVER=uvicorn` for
# hot-reload during local development) without two separate scripts.
# Default is gunicorn — that's what production wants.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ── Check Ollama is running ──────────────────────────────────────────────────
if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
    warn "Ollama is not running. Starting it..."
    ollama serve &>/dev/null &
    sleep 3
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama started"
    else
        fail "Could not start Ollama. Run 'ollama serve' in a separate terminal."
        exit 1
    fi
else
    ok "Ollama is running"
fi

# ── Activate venv ────────────────────────────────────────────────────────────
if [[ ! -d "venv" ]]; then
    fail "Python venv not found. Run 'bash scripts/setup.sh' first."
    exit 1
fi
source venv/bin/activate
ok "Virtual environment activated"

# ── Pick server (gunicorn for prod, uvicorn for dev) ────────────────────────
SERVER="${SERVER:-gunicorn}"

mkdir -p logs
LOG_FILE="logs/server.log"

# ── Stop any existing instance on port 8000 ─────────────────────────────────
# (Cheap safety net — stop.sh is the proper way, but this also helps when
# the user runs start.sh directly without stop.sh first.)
if lsof -ti:8000 &>/dev/null; then
    warn "Port 8000 already in use — killing old process"
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    sleep 1
fi

echo ""
echo "  🚀 Starting Video Learning App (server: $SERVER)..."
echo "  📡 http://localhost:8000"
echo "  📚 Docs: http://localhost:8000/docs"
echo "  📝 Logs: $LOG_FILE"
echo "  ⏹️  Stop: bash scripts/stop.sh"
echo ""

# ── Disable macOS proxy auto-detection (Day 9 hotfix) ─────────────────────
# Without NO_PROXY=*, every outbound HTTPS call (Groq, Ollama, Firebase,
# YouTube, Cloudflare) goes through macOS's SCDynamicStore, which crashes
# Python 3.14 with EXC_GUARD (bug_type=309). This was the cause of
# worker SIGSEGVs starting 2026-08-26 — same crash signature across 3+ days.
#
# NO_PROXY=* tells httpx/urllib3 "skip system proxy discovery for ALL hosts"
# — we don't use an HTTP proxy in this stack (Cloudflare Tunnel handles
# ingress). Setting it globally before exec'ing gunicorn ensures every
# worker inherits it.
#
# See doc/runbook-day6.md § "Worker keeps restarting (timeout=60s)" for
# the SIGSEGV observation; see doc/mvp2-final-go-live-plan.md Day 9 for
# the root-cause writeup.
export NO_PROXY="*"
export no_proxy="*"  # belt + suspenders (some libs check lowercase)

# ── Start ───────────────────────────────────────────────────────────────────
if [[ "$SERVER" == "gunicorn" ]]; then
    # Production: gunicorn process manager with uvicorn workers
    exec gunicorn -c gunicorn.conf.py app.main:app \
        2>&1 | tee -a "$LOG_FILE"
else
    # Dev: uvicorn with hot-reload. Single process, single thread.
    # h11_max_incomplete_event_size bumped to 64 MB so large multipart
    # uploads (e.g. 3+ files at 1+ GB) don't trip h11's default 16 KB
    # buffer. See doc/MVP2.0-Status.md §19.
    exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 \
        --h11-max-incomplete-event-size 67108864 \
        2>&1 | tee -a "$LOG_FILE"
fi
