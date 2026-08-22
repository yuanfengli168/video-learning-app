#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# uninstall-backups.sh — Remove backup LaunchDaemons
# Idempotent: safe to run multiple times.
#
# Usage:    bash scripts/uninstall-backups.sh
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

PLIST_DIR="$HOME/Library/LaunchAgents"
SCRIPTS=("daily" "db" "monthly" "verify")

GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }

for name in "${SCRIPTS[@]}"; do
    label="com.videoapp.backup-${name}"
    plist="${PLIST_DIR}/${label}.plist"
    if [ -f "$plist" ]; then
        launchctl unload "$plist" 2>/dev/null || true
        rm -f "$plist"
        ok "Unloaded + removed $label"
    else
        echo "  (no plist for $label, skipping)"
    fi
done

ok "Uninstall complete. Re-run scripts/setup-backups.sh to restore."
