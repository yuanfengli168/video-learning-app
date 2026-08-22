#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# backup-daily.sh — Snapshot NVMe → RAID 1 HDD nightly at 00:00 SGT
# Called by:  com.videoapp.backup-daily.plist (LaunchDaemon)
#
# See doc/mvp2-storage-architecture.md for design + rationale.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────
DATE=$(date +%Y-%m-%d)
DEST="/Volumes/Storage-Backup-HDD/snapshot-${DATE}"
SRC_FAST="/Volumes/Storage-Fast-NVMe"
SRC_MEDIUM="/Volumes/Storage-Medium-NVMe/video-app"
LOG="$HOME/Library/Logs/video-app-backup.log"
RETENTION_DAILY=30  # keep last 30 daily snapshots

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# ── Pre-flight ────────────────────────────────────────────────────────────
if [ ! -d "/Volumes/Storage-Backup-HDD" ]; then
    log "ERROR: Storage-Backup-HDD not mounted — skipping backup"
    exit 1
fi

if [ ! -d "$SRC_FAST" ] || [ ! -d "$SRC_MEDIUM" ]; then
    log "ERROR: source NVMe volumes missing — skipping backup"
    exit 1
fi

# Idempotent: if today's snapshot exists, exit cleanly
if [ -d "$DEST" ]; then
    log "Snapshot $DEST already exists, skipping"
    exit 0
fi

# Acquire lock so two backups don't run at once (LaunchDaemon should serialize,
# but belt-and-suspenders for ad-hoc manual runs).
LOCKFILE="/Volumes/Storage-Backup-HDD/snapshots.lock"
if [ -e "$LOCKFILE" ]; then
    log "ERROR: lockfile exists at $LOCKFILE — another backup may be running"
    exit 1
fi
trap "rm -f $LOCKFILE" EXIT
touch "$LOCKFILE"

# ── Snapshot ──────────────────────────────────────────────────────────────
log "Starting daily snapshot → $DEST"
mkdir -p "$DEST/fast" "$DEST/medium" "$DEST/db-backup"

START=$(date +%s)

# rsync NVMe → RAID (without --delete so snapshots are frozen points in time)
# Exclude macOS system dirs (.Spotlight-V100, .fseventsd, .Trashes) — these
# are unreadable from user-space and cause rsync exit code 23 (partial).
RSYNC_EXCLUDES=(
    --exclude=".Spotlight-V100"
    --exclude=".fseventsd"
    --exclude=".Trashes"
    --exclude=".TemporaryItems"
)
rsync -a --quiet "${RSYNC_EXCLUDES[@]}" "$SRC_FAST/"  "$DEST/fast/" || { log "rsync fast failed"; exit 2; }
rsync -a --quiet "${RSYNC_EXCLUDES[@]}" "$SRC_MEDIUM/"  "$DEST/medium/" || { log "rsync medium failed"; exit 2; }

# SQLite hot backup (consistent even with active writes via .backup API)
if [ -f "$SRC_FAST/video_learning.db" ]; then
    sqlite3 "$SRC_FAST/video_learning.db" \
        ".backup '$DEST/db-backup/video_learning-${DATE}.sqlite3'" \
        || log "WARN: sqlite .backup failed (non-fatal, rsync copy still good)"
fi

END=$(date +%s)
DURATION=$((END - START))
SIZE=$(du -sh "$DEST" | awk '{print $1}')

log "Snapshot complete: $DEST ($SIZE, ${DURATION}s)"

# ── Prune: keep only last $RETENTION_DAILY daily snapshots ────────────────
cd /Volumes/Storage-Backup-HDD
SNAPSHOTS=$(ls -dt snapshot-*/ 2>/dev/null | wc -l | tr -d ' ')
if [ "$SNAPSHOTS" -gt "$RETENTION_DAILY" ]; then
    REMOVE_COUNT=$((SNAPSHOTS - RETENTION_DAILY))
    log "Pruning $REMOVE_COUNT old snapshots (keeping last $RETENTION_DAILY)"
    ls -dt snapshot-*/ | tail -n "$REMOVE_COUNT" | while read -r old; do
        log "  removing $old"
        rm -rf "$old"
    done
fi

log "Daily backup OK"
