#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_firebase_key.sh — Move downloaded Firebase service account key to project root
# Usage:  bash scripts/setup_firebase_key.sh
#
# Run this AFTER downloading the key from Firebase Console:
#   Firebase Console → Project Settings → Service Accounts → Generate New Private Key
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
DEST="$PROJECT_ROOT/firebase-service-account.json"

# Already exists?
if [[ -f "$DEST" ]]; then
    ok "firebase-service-account.json already exists at project root"
    echo "  Path: $DEST"
    echo "  Size: $(wc -c < "$DEST") bytes"
    exit 0
fi

# Look for the downloaded file in common locations
DOWNLOADS="$HOME/Downloads"
PATTERNS=(
    "$DOWNLOADS/*firebase-adminsdk*.json"
    "$DOWNLOADS/*service-account*.json"
    "$PROJECT_ROOT/*firebase-adminsdk*.json"
)

FOUND=""
for pattern in "${PATTERNS[@]}"; do
    # shellcheck disable=SC2206
    matches=($pattern)
    if [[ ${#matches[@]} -gt 0 && -f "${matches[0]}" ]]; then
        FOUND="${matches[0]}"
        break
    fi
done

if [[ -n "$FOUND" ]]; then
    warn "Found Firebase key: $FOUND"
    echo "  Moving to: $DEST"
    mv "$FOUND" "$DEST"
    ok "Done! Service account key is in place."
else
    fail "Could not find the Firebase service account key JSON."
    echo ""
    echo "  Steps to get it:"
    echo "  1. Go to Firebase Console → Project Settings → Service Accounts"
    echo "  2. Click 'Generate new private key' — downloads a JSON file"
    echo "  3. Run this script again:"
    echo "     bash scripts/setup_firebase_key.sh"
    echo ""
    echo "  Or manually move the file:"
    echo "     mv ~/Downloads/<your-project>-firebase-adminsdk-*.json \\"
    echo "       \"$DEST\""
    exit 1
fi