#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-probe.sh — Run the backup health probe every 5 minutes
# Called by:  com.videoapp.backup-probe.plist (LaunchAgent)
#
# What it does:
#   1. Runs app.services.backup_monitor.collect_status() in the venv
#   2. Writes /tmp/video-app-backup-status.json
#   3. Logs a one-line summary to video-app-backup.log
#
# Why every 5 min?
#   - cheap (one launchctl print per job + one diskutil + one stat)
#   - within 5 min of any backup failure, the dashboard / /api/ready
#     / admin events page will reflect the new state
#   - launchd will throttle runaway jobs anyway
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="$HOME/Library/Logs/video-app-backup.log"

log() { echo "[$(date '+%F %T')] [probe] $*" | tee -a "$LOG"; }

if [[ ! -d "$PROJECT_ROOT/venv" ]]; then
    log "ERROR: venv not found at $PROJECT_ROOT/venv"
    exit 1
fi

cd "$PROJECT_ROOT"
source venv/bin/activate

# Run the probe. main() returns 0 on success, 1 on internal failure.
# Note: a successful run that finds the backups are unhealthy will
# STILL return 0 (the failure is in the JSON, not the probe).
python -m app.services.backup_monitor >> "$LOG" 2>&1 || {
    log "ERROR: probe exited non-zero"
    exit 1
}

log "probe OK"
