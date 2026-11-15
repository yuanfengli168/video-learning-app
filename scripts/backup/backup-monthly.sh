#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-monthly.sh — Long-term monthly archive (1st of month at 00:30 SGT)
# Called by:  com.videoapp.backup-monthly.plist (LaunchDaemon)
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

DATE=$(date +%Y-%m)
DEST="/Volumes/Storage-Backup-HDD/monthly-${DATE}"
SRC_FAST="/Volumes/Storage-Fast-NVMe"
SRC_MEDIUM="/Volumes/Storage-Medium-NVMe/video-app"
LOG="$HOME/Library/Logs/video-app-backup.log"
RETENTION_MONTHLY=12  # keep last 12 months

log() { echo "[$(date '+%F %T')] [monthly] $*" | tee -a "$LOG"; }

if [ ! -d "/Volumes/Storage-Backup-HDD" ]; then
    log "ERROR: Storage-Backup-HDD not mounted — skipping"
    exit 1
fi

if [ -d "$DEST" ]; then
    log "Archive $DEST already exists, skipping"
    exit 0
fi

log "Starting monthly archive → $DEST"
mkdir -p "$DEST/fast" "$DEST/medium"

START=$(date +%s)
RSYNC_EXCLUDES=(
    --exclude=".Spotlight-V100"
    --exclude=".fseventsd"
    --exclude=".Trashes"
    --exclude=".TemporaryItems"
)
rsync -a --quiet "${RSYNC_EXCLUDES[@]}" "$SRC_FAST/"   "$DEST/fast/"  || { log "rsync fast failed"; exit 2; }
rsync -a --quiet "${RSYNC_EXCLUDES[@]}" "$SRC_MEDIUM/" "$DEST/medium/" || { log "rsync medium failed"; exit 2; }

END=$(date +%s)
log "Monthly archive complete: $DEST ($((END - START))s, $(du -sh "$DEST" | awk '{print $1}'))"

# Prune old monthly archives
cd /Volumes/Storage-Backup-HDD
ARCHIVES=$(ls -dt monthly-*/ 2>/dev/null | wc -l | tr -d ' ')
if [ "$ARCHIVES" -gt "$RETENTION_MONTHLY" ]; then
    REMOVE_COUNT=$((ARCHIVES - RETENTION_MONTHLY))
    log "Pruning $REMOVE_COUNT old monthly archives"
    ls -dt monthly-*/ | tail -n "$REMOVE_COUNT" | while read -r old; do
        log "  removing $old"
        rm -rf "$old"
    done
fi
