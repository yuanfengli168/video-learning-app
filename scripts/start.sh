#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Start the Video Learning App locally
# Usage:  bash scripts/start.sh
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

# ── Start the app ────────────────────────────────────────────────────────────
echo ""
echo "  🚀 Starting Video Learning App..."
echo "  📡 http://localhost:8000"
echo "  📚 Docs: http://localhost:8000/docs"
echo "  ⏹️  Press Ctrl+C to stop"
echo ""

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000