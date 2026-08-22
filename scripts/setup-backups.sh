#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# setup-backups.sh — Create + load LaunchDaemons for backup scripts
# Idempotent: safe to run multiple times.
#
# Usage:    bash scripts/setup-backups.sh
# Purpose:  Run this on a NEW machine (e.g., Mac Studio) after `git pull`
#           to set up the nightly backup schedule.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
SCRIPTS=("daily" "db" "monthly" "verify")

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

# ── Pre-flight ────────────────────────────────────────────────────────────
if [ ! -d "$PROJECT_DIR/scripts/backup" ]; then
    fail "Backup scripts not found at $PROJECT_DIR/scripts/backup/"
    exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

# ── Verify backup volumes exist ───────────────────────────────────────────
for vol in Storage-Fast-NVMe Storage-Medium-NVMe Storage-Backup-HDD; do
    if [ ! -d "/Volumes/$vol" ]; then
        warn "/Volumes/$vol not mounted"
        echo "    The backup will skip if the RAID is not present."
        echo "    Plug in the Acasis H006 enclosure and verify all 3 volumes mount."
    else
        ok "/Volumes/$vol mounted"
    fi
done

# ── Generate plist for each script ────────────────────────────────────────
for name in "${SCRIPTS[@]}"; do
    label="com.videoapp.backup-${name}"
    plist="${PLIST_DIR}/${label}.plist"
    script_path="${PROJECT_DIR}/scripts/backup/backup-${name}.sh"

    # Build StartCalendarInterval based on schedule
    case "$name" in
        daily)
            # 00:00 every day
            calendar='
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>0</integer>
        <key>Minute</key><integer>0</integer>
    </dict>'
            ;;
        db)
            # Every 6 hours: 00:00, 06:00, 12:00, 18:00
            calendar='
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    </array>'
            ;;
        monthly)
            # 1st of month at 00:30
            calendar='
    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key><integer>1</integer>
        <key>Hour</key><integer>0</integer>
        <key>Minute</key><integer>30</integer>
    </dict>'
            ;;
        verify)
            # Sunday 01:00
            calendar='
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>1</integer>
        <key>Minute</key><integer>0</integer>
    </dict>'
            ;;
    esac

    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${script_path}</string>
    </array>
${calendar}
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/video-app-backup.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/video-app-backup.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF
    ok "Wrote $plist"

    # Unload first (in case stale load), then load
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    ok "Loaded $label"
done

echo ""
ok "Setup complete. Schedule:"
echo "    Daily snapshot:     00:00 SGT"
echo "    DB hot backup:      00:00, 06:00, 12:00, 18:00 SGT"
echo "    Monthly archive:    1st of month at 00:30 SGT"
echo "    Weekly verify:      Sunday 01:00 SGT"
echo ""
echo "Verify with:    launchctl list | grep videoapp"
echo "Uninstall with: scripts/uninstall-backups.sh"
