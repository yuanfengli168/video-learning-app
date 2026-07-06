#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — Check and install prerequisites for Video Learning App (macOS)
# Usage:  bash scripts/setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }
ok()      { echo -e "${GREEN}✅ $1${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()    { echo -e "${RED}❌ $1${NC}"; }
section() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}"; }

# ── 1. Homebrew ──────────────────────────────────────────────────────────────
section "1/5  Homebrew"
if command -v brew &>/dev/null; then
    ok "Homebrew installed: $(brew --version | head -1)"
else
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add to PATH for this session
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
fi

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────────
section "2/5  Python 3.11+"
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]]; then
        ok "Python $PY_VERSION installed"
    else
        fail "Python $PY_VERSION found, need 3.11+"
        warn "Installing Python 3.11 via Homebrew..."
        brew install python@3.11
        ok "Python 3.11 installed"
    fi
else
    fail "Python3 not found"
    warn "Installing Python via Homebrew..."
    brew install python@3.11
    ok "Python installed"
fi

# ── 3. FFmpeg ────────────────────────────────────────────────────────────────
section "3/5  FFmpeg"
if command -v ffmpeg &>/dev/null; then
    ok "FFmpeg installed: $(ffmpeg -version | head -1)"
else
    warn "FFmpeg not found. Installing via Homebrew..."
    brew install ffmpeg
    ok "FFmpeg installed"
fi

# ── 4. Ollama ───────────────────────────────────────────────────────────────
section "4/5  Ollama"
if command -v ollama &>/dev/null; then
    ok "Ollama installed: $(ollama --version 2>/dev/null || echo 'installed')"
else
    warn "Ollama not found. Installing..."
    brew install ollama
    ok "Ollama installed"
fi

# Check if Ollama is running
if curl -s http://localhost:11434/api/tags &>/dev/null; then
    ok "Ollama is running at http://localhost:11434"
else
    warn "Ollama is not running. Starting it..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!
    sleep 3
    if curl -s http://localhost:11434/api/tags &>/dev/null; then
        ok "Ollama started (PID $OLLAMA_PID)"
    else
        fail "Could not start Ollama. Run 'ollama serve' manually."
    fi
fi

# Check if glm-5.2:cloud model is available
info "Checking for glm-5.2:cloud model..."
if ollama list 2>/dev/null | grep -q "glm-5.2:cloud"; then
    ok "Model glm-5.2:cloud is available"
else
    warn "Model glm-5.2:cloud not found. Pulling (this may take a few minutes)..."
    ollama pull glm-5.2:cloud
    ok "Model glm-5.2:cloud pulled"
fi

# ── 5. Firebase Service Account Key ─────────────────────────────────────────
section "5/5  Firebase Service Account Key"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
KEY_FILE="$PROJECT_ROOT/firebase-service-account.json"

if [[ -f "$KEY_FILE" ]]; then
    ok "Service account key found: firebase-service-account.json"
else
    warn "Service account key not found at project root."
    echo ""
    echo "  To get it:"
    echo "  1. Go to Firebase Console → Project Settings → Service Accounts"
    echo "  2. Click 'Generate new private key' — downloads a JSON file"
    echo "  3. Move it to the project root:"
    echo ""
    echo "     mv ~/Downloads/<your-project>-firebase-adminsdk-*.json \\"
    echo "       \"$PROJECT_ROOT/firebase-service-account.json\""
    echo ""
    echo "  Or run this after downloading:"
    echo "     bash scripts/setup_firebase_key.sh"
    echo ""
fi

# ── Bonus: Python venv + dependencies ────────────────────────────────────────
section "Bonus  Python venv + dependencies"
if [[ ! -d "$PROJECT_ROOT/venv" ]]; then
    warn "Creating Python virtual environment..."
    python3 -m venv "$PROJECT_ROOT/venv"
    ok "Virtual environment created"
else
    ok "Virtual environment exists"
fi

info "Installing Python dependencies..."
source "$PROJECT_ROOT/venv/bin/activate"
pip install -q -r "$PROJECT_ROOT/requirements.txt" 2>&1 | tail -1
ok "Python dependencies installed"

# ── Summary ──────────────────────────────────────────────────────────────────
section "Summary"
echo ""
echo "  To start the app:"
echo "    source venv/bin/activate"
echo "    uvicorn app.main:app --reload"
echo ""
echo "  Then visit: http://localhost:8000"
echo ""
ok "Setup complete!"