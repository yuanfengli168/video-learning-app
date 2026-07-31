#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start-ios.sh — Start the Video Learning App on https://localhost:8443
#                 for the iOS Pocket app.
# Usage:  bash scripts/start-ios.sh
#
# Difference from start.sh:
#   start.sh     — plain HTTP on :8000 for the Mac web app (browser)
#   start-ios.sh — HTTPS on     :8443 for the iOS Pocket app (uses mkcert)
#
# Both can run simultaneously (different ports) — the iOS app only talks
# to :8443, the Mac browser only talks to :8000. Run whichever you need.
#
# Requires: certs/localhost.pem + certs/localhost-key.pem (run
# `bash scripts/setup.sh` if missing — it calls `mkcert -install` and
# generates the certs).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

PORT=8443
CERT="$PROJECT_ROOT/certs/localhost.pem"
KEY="$PROJECT_ROOT/certs/localhost-key.pem"

# ── Pre-flight checks ────────────────────────────────────────────────────────
# Refuse to start if certs are missing. We don't auto-generate them
# because `mkcert -install` mutates the Mac keychain (interactive +
# sudo on some setups); better to fail loud than silently skip SSL.

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
    fail "mkcert certs not found at:"
    fail "  $CERT"
    fail "  $KEY"
    echo ""
    warn "Generate them once with:"
    warn "  brew install mkcert nss   # if you don't have mkcert"
    warn "  mkcert -install           # trust the local CA in macOS keychain"
    warn "  mkcert -key-file $KEY -cert-file $CERT localhost 127.0.0.1 ::1 <LAN_IP> [ts-hostname]"
    echo ""
    warn "  <LAN_IP> = your Mac's LAN IP (run: ipconfig getifaddr en0)"
    warn "  [ts-hostname] = optional Tailscale hostname (run: tailscale status)"
    warn "  Both let real iPhones verify the cert; localhost-only won't work off-simulator."
    echo ""
    fail "Aborting — fix the certs, then re-run."
    exit 1
fi

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

# ── Check if 8443 is already in use ───────────────────────────────────────────
if lsof -ti:$PORT &>/dev/null; then
    warn "Port $PORT is already in use. Kill the existing server first:"
    warn "  bash scripts/stop.sh          # stops the :8000 server"
    warn "  lsof -ti:$PORT | xargs kill   # force-kill whatever holds $PORT"
    exit 1
fi

# ── Activate venv ────────────────────────────────────────────────────────────
if [[ ! -d "venv" ]]; then
    fail "Python venv not found. Run 'bash scripts/setup.sh' first."
    exit 1
fi

source venv/bin/activate
ok "Virtual environment activated"

# ── Start the app on 8443 + SSL ──────────────────────────────────────────────
mkdir -p logs
LOG_FILE="logs/server-ios.log"

echo ""
echo "  🚀 Starting Video Learning App for iOS..."
echo "  📡 https://localhost:$PORT"
echo "  📚 Docs: https://localhost:$PORT/docs"
echo "  📝 Logs: $LOG_FILE"
echo "  ⏹️  Press Ctrl+C to stop"
echo ""
echo "  ℹ️  The iOS Pocket app points at https://localhost:$PORT"
echo "  ℹ️  (start.sh runs a separate server on http://localhost:8000 —"
echo "       both can run simultaneously; they share the same database)"
echo ""

# h11_max_incomplete_event_size: same reason as start.sh — see that
# file's comment + doc/MVP2.0-Status.md §19 for the postmortem.
uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT" \
    --ssl-keyfile "$KEY" \
    --ssl-certfile "$CERT" \
    --h11-max-incomplete-event-size 67108864 \
    2>&1 | tee -a "$LOG_FILE"