#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-db.sh — SQLite hot backup every 6 hours
# Called by:  com.videoapp.backup-db.plist (LaunchDaemon)
# Runs at:    00:00, 06:00, 12:00, 18:00 SGT
#
# Why more frequent than the daily snapshot?
# The DB is the most critical single file. Losing 24h of chat messages / admin
# actions is bad. Losing 6h is much better. SQLite's .backup command takes a
# consistent snapshot even with active writes via the online backup API.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

DATE=$(date +%Y-%m-%d-%H%M)
DEST="/Volumes/Storage-Backup-HDD/db-backup"
DB="/Volumes/Storage-Fast-NVMe/video_learning.db"
LOG="$HOME/Library/Logs/video-app-backup.log"
RETENTION_DB=28  # 7 days × 4 per day = 28 hot backups kept

log() { echo "[$(date '+%F %T')] [db] $*" | tee -a "$LOG"; }

if [ ! -d "/Volumes/Storage-Backup-HDD" ]; then
    log "ERROR: Storage-Backup-HDD not mounted"
    exit 1
fi

if [ ! -f "$DB" ]; then
    log "ERROR: $DB not found"
    exit 1
fi

mkdir -p "$DEST"

log "Hot backup → ${DEST}/video_learning-${DATE}.sqlite3"
sqlite3 "$DB" ".backup '${DEST}/video_learning-${DATE}.sqlite3'" || {
    log "ERROR: sqlite .backup failed"
    exit 2
}

# Verify
RESULT=$(sqlite3 "${DEST}/video_learning-${DATE}.sqlite3" "PRAGMA integrity_check;" 2>&1)
log "Integrity: $RESULT"

# Prune: keep last $RETENTION_DB DB backups
cd "$DEST"
COUNT=$(ls -t video_learning-*.sqlite3 2>/dev/null | wc -l | tr -d ' ')
if [ "$COUNT" -gt "$RETENTION_DB" ]; then
    REMOVE_COUNT=$((COUNT - RETENTION_DB))
    log "Pruning $REMOVE_COUNT old DB backups"
    ls -t video_learning-*.sqlite3 | tail -n "$REMOVE_COUNT" | while read -r old; do
        rm -f "$old"
    done
fi

log "DB hot backup OK"
