#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup-env.sh — Interactive .env setup wizard for Video Learning App
# Usage:  bash scripts/setup-env.sh
#
# What it does:
#   1. Checks if .env already exists (warns if so)
#   2. Copies .env.example → .env if missing
#   3. Prompts you for the 6 Firebase values + Ollama model
#   4. Validates each value isn't empty / looks right
#   5. Writes the final .env
#
# Where to get the values:
#   Firebase Console → Project Settings → General → "Your apps" → Web app
#   (or "Add app" if no web app exists yet)
#   You'll see: apiKey, authDomain, projectId, storageBucket,
#   messagingSenderId, appId
#
# Ollama model: defaults to glm-5.2:cloud (cloud-hosted, no local GPU needed)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }
info()  { echo -e "${CYAN}ℹ️  $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ENV_FILE="$PROJECT_ROOT/.env"
EXAMPLE_FILE="$PROJECT_ROOT/.env.example"

# ── Step 1: warn if .env already exists ──────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    warn ".env already exists at $ENV_FILE"
    echo ""
    echo "  Refusing to overwrite. Choose one:"
    echo "    1) Edit the existing .env manually:    open $ENV_FILE"
    echo "    2) Backup + re-run this wizard:        mv $ENV_FILE $ENV_FILE.bak && bash $0"
    echo "    3) Force overwrite (lose old values):  rm $ENV_FILE && bash $0"
    echo ""
    read -rp "Continue anyway? (y/N) " force
    [[ "$force" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
    mv "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
    warn "Backed up existing .env to .env.bak.$(date +%s)"
fi

# ── Step 2: copy from .env.example if missing ────────────────────────
if [[ ! -f "$EXAMPLE_FILE" ]]; then
    fail ".env.example missing at $EXAMPLE_FILE — repo is incomplete."
    exit 1
fi

cp "$EXAMPLE_FILE" "$ENV_FILE"
ok "Copied .env.example → .env"

# ── Step 3: interactive prompts ─────────────────────────────────────
echo ""
echo "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo "${BOLD}║  Video Learning App — .env setup wizard                     ║${NC}"
echo "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
info "Where to find these values:"
info "  Firebase Console → Project Settings → General → Your apps → Web app"
info "  (If no Web app: click </> to register one. No hosting needed.)"
echo ""

# Helper: prompt with validation
prompt() {
    local var_name="$1"
    local label="$2"
    local default="$3"
    local current
    current="$(grep -E "^${var_name}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
    [[ -n "$current" && "$current" != "your-"* && "$current" != "" ]] && default="$current"

    local value
    while true; do
        if [[ -n "$default" ]]; then
            read -rp "  $label [${default}]: " value
            value="${value:-$default}"
        else
            read -rp "  $label: " value
        fi
        if [[ -n "$value" ]]; then
            break
        fi
        warn "  $label cannot be empty."
    done
    echo "$value"
}

FIREBASE_API_KEY="$(prompt FIREBASE_API_KEY "Firebase apiKey (starts with 'AIza')")"
FIREBASE_AUTH_DOMAIN="$(prompt FIREBASE_AUTH_DOMAIN "Firebase authDomain (e.g. your-app.firebaseapp.com)")"
FIREBASE_PROJECT_ID="$(prompt FIREBASE_PROJECT_ID "Firebase projectId (no spaces)")"
FIREBASE_STORAGE_BUCKET="$(prompt FIREBASE_STORAGE_BUCKET "Firebase storageBucket (e.g. your-app.appspot.com)")"
FIREBASE_MESSAGING_SENDER_ID="$(prompt FIREBASE_MESSAGING_SENDER_ID "Firebase messagingSenderId (digits)")"
FIREBASE_APP_ID="$(prompt FIREBASE_APP_ID "Firebase appId (1:123:web:abc)")"

OLLAMA_BASE_URL="$(prompt OLLAMA_BASE_URL "Ollama base URL" "http://localhost:11434")"
OLLAMA_MODEL="$(prompt OLLAMA_MODEL "Ollama model name" "glm-5.2:cloud")"

# ── Step 4: write the .env ──────────────────────────────────────────
# Use python for safe in-place edit (BSD sed on macOS is quirky)
python3 - "$ENV_FILE" <<EOF
import sys, re
env_file = sys.argv[1]

updates = {
    "FIREBASE_API_KEY":               "${FIREBASE_API_KEY}",
    "FIREBASE_AUTH_DOMAIN":           "${FIREBASE_AUTH_DOMAIN}",
    "FIREBASE_PROJECT_ID":            "${FIREBASE_PROJECT_ID}",
    "FIREBASE_STORAGE_BUCKET":        "${FIREBASE_STORAGE_BUCKET}",
    "FIREBASE_MESSAGING_SENDER_ID":   "${FIREBASE_MESSAGING_SENDER_ID}",
    "FIREBASE_APP_ID":                "${FIREBASE_APP_ID}",
    "OLLAMA_BASE_URL":                "${OLLAMA_BASE_URL}",
    "OLLAMA_MODEL":                   "${OLLAMA_MODEL}",
}

with open(env_file, "r") as f:
    lines = f.readlines()

new_lines = []
seen = set()
for line in lines:
    m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
    if m and m.group(1) in updates:
        key = m.group(1)
        new_lines.append(f"{key}={updates[key]}\n")
        seen.add(key)
    else:
        new_lines.append(line)

# Append any keys not already in the file
for key, val in updates.items():
    if key not in seen:
        new_lines.append(f"{key}={val}\n")

with open(env_file, "w") as f:
    f.writelines(new_lines)
EOF
ok "Wrote .env"

# ── Step 5: validate ────────────────────────────────────────────────
echo ""
echo "${BOLD}Validating .env…${NC}"
empty_count=0
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    if [[ "$value" == "your-"* || -z "$value" ]]; then
        warn "  $key is still empty / placeholder"
        empty_count=$((empty_count+1))
    fi
done < "$ENV_FILE"

if [[ $empty_count -gt 0 ]]; then
    warn "$empty_count value(s) are still placeholders. AuthKit won't work until you fill them in."
    echo ""
    echo "  Open $ENV_FILE in your editor and fill in:"
    echo "    open $ENV_FILE"
    echo ""
    exit 1
fi

ok ".env is complete"

# ── Step 6: reminder to restart server ──────────────────────────────
echo ""
echo "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo "${BOLD}║  Next steps                                                  ║${NC}"
echo "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  1. Restart the server so pydantic-settings re-reads .env:"
echo "       bash scripts/stop.sh && bash scripts/start.sh"
echo ""
echo "  2. Open http://localhost:8000/login in your browser"
echo "     → AuthKit widget should render with Google + Email buttons"
echo ""
echo "  3. (Optional) Verify the server can talk to Firebase:"
echo "       curl -s http://localhost:8000/login | grep -o 'AIza[^\"]*' | head -1"
echo "     → should print your apiKey, confirming the env var made it through"
echo ""
ok ".env setup complete"
