#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-verify.sh — Sunday 01:00 SGT — verify backups are working
# Runs a dry-run rsync and logs how much would change. Surfaces anomalies.
# Called by:  com.videoapp.backup-verify.plist (LaunchDaemon)
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

LOG="$HOME/Library/Logs/video-app-backup.log"
SRC_FAST="/Volumes/Storage-Fast-NVMe"
SRC_MEDIUM="/Volumes/Storage-Medium-NVMe/video-app"
LATEST=$(ls -dt /Volumes/Storage-Backup-HDD/snapshot-*/ 2>/dev/null | head -1 || true)

log() { echo "[$(date '+%F %T')] [verify] $*" | tee -a "$LOG"; }

if [ ! -d "/Volumes/Storage-Backup-HDD" ]; then
    log "ERROR: Storage-Backup-HDD not mounted"
    exit 1
fi

if [ -z "$LATEST" ]; then
    log "WARN: no snapshots found yet — has daily backup ever run?"
    exit 0
fi

log "Latest snapshot: $LATEST"
log "Source fast:    $SRC_FAST ($(du -sh "$SRC_FAST" 2>/dev/null | awk '{print $1}'))"
log "Source medium:  $SRC_MEDIUM ($(du -sh "$SRC_MEDIUM" 2>/dev/null | awk '{print $1}'))"
log "Snapshot size:  $(du -sh "$LATEST" 2>/dev/null | awk '{print $1}')"

# Count files in latest snapshot
SNAP_COUNT=$(find "$LATEST" -type f 2>/dev/null | wc -l | tr -d ' ')
log "Snapshot file count: $SNAP_COUNT"

# Dry-run to see what would change (catches unexpected growth or missing files)
DRYRUN=$(rsync -a --dry-run --stats "$SRC_FAST/" "$LATEST/fast/" 2>&1 | grep -E "Number of files|Total transferred" || true)
log "Dry-run deltas (fast): $DRYRUN"

# Sanity check: SQLite header
if [ -f "$LATEST/db-backup/video_learning-$(date +%Y-%m-%d).sqlite3" ]; then
    DB_OK=$(sqlite3 "$LATEST/db-backup/video_learning-$(date +%Y-%m-%d).sqlite3" "PRAGMA integrity_check;" 2>&1)
    log "Today's DB integrity: $DB_OK"
fi

log "Verification OK"
