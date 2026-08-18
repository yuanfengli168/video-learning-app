#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# uninstall-launchdaemon.sh — Remove the video-learning-app LaunchDaemon
# Usage:  sudo bash scripts/uninstall-launchdaemon.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()    { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "${RED}❌ $1${NC}"; }

if [[ $EUID -ne 0 ]]; then
    fail "Must be run as root. Use:  sudo bash $0"
    exit 1
fi

PLIST_PATH="/Library/LaunchDaemons/com.video-learning-app.plist"

if [[ ! -f "$PLIST_PATH" ]]; then
    warn "Plist not found at $PLIST_PATH — nothing to do."
    exit 0
fi

# Unload first
launchctl unload "$PLIST_PATH" 2>/dev/null && ok "Unloaded from launchd" || warn "Could not unload (already not loaded?)"

# Remove the plist
rm -f "$PLIST_PATH"
ok "Removed $PLIST_PATH"

# Note: we do NOT remove logs from ~/Library/Logs/ — those are
# diagnostic and you might want them. Remove manually if desired.

echo ""
ok "LaunchDaemon uninstalled"
echo ""
echo "The server may still be running if you started it via ./scripts/start.sh."
echo "To stop any running instance:  bash scripts/stop.sh"
