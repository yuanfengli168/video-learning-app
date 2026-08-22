# MVP2 Storage Architecture — Hardware, RAID, Backup

> **Status**: Planning. No code yet.
> **Last updated**: 2026-08-22
> **Owner**: jackyli
> **Related**: `mvp2-final-go-live-plan.md`

This doc captures the storage layer design for the MVP2 go-live: what physical
drives we have, how they're partitioned/RAID'd, where the app's data lives, and
how it gets backed up. It is the **single source of truth** for storage decisions.

---

## 1. Hardware inventory

Acasis H006 Thunderbolt 3 enclosure connected to MBP (M1 Max, 32 GB RAM).

| Disk | Model | Capacity | Protocol | Role |
|---|---|---|---|---|
| `/dev/disk10` | Samsung SSD 990 PRO 1TB | 1.0 TB | NVMe (Thunderbolt) | Hot DB + scratch |
| `/dev/disk11` | Lexar SSD NM610 PRO 2TB | 2.0 TB | NVMe (Thunderbolt) | Warm user assets |
| `/dev/disk12` | 3.5" HDD #1 (TBD model) | 3.0 TB | USB (SATA bridge) | Backup mirror A |
| `/dev/disk13` | 3.5" HDD #2 (TBD model) | 3.0 TB | USB (SATA bridge) | Backup mirror B |

**Total raw**: 9 TB. **Total usable after RAID 1**: 6 TB (1+2+3).

> The 2 NVMe SSDs are **different sizes** — 990 Pro is 1 TB, Lexar is 2 TB.
> They **cannot be RAID'd** together (RAID requires identical members). They
> are independent drives.

> The 2 HDDs are **identical 3 TB capacity** — perfect for RAID 1 mirror.

### Power loss caveat

USB-SATA bridges (HDDs) have **no battery-backed cache**. A write in flight at
the moment of power loss can corrupt the filesystem on both drives of a RAID 1.
Mitigations:

1. UPS on the MBP (strongly recommended for production)
2. APFS journaling (replays to consistency on next mount)
3. Don't unplug the H006 during writes (the backup cron runs at 00:00 SGT,
   MBP is presumed plugged in)

---

## 2. Layer model

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 (live, working)  ──────►  990 Pro + Lexar NVMe         │
│   - App reads/writes here                                       │
│   - Fast, no fault tolerance (single drive each)                │
│   - If a drive dies: service down until restore from Layer 2    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ nightly cron at 00:00 SGT
                              │ rsync NVMe ──► HDD RAID 1
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 (backup, lagging)  ────►  HDD RAID 1 (Storage-Backup) │
│   - Read-only from app's perspective                            │
│   - Tolerates 1 HDD dying (mirror keeps data)                   │
│   - Always 0-24 hours behind Layer 1                            │
└─────────────────────────────────────────────────────────────────┘
```

**Key principle**: the app **never writes directly to the backup layer**. The
backup is always a snapshot taken by a cron job. This means:
- Accidental `rm -rf` on Layer 1 doesn't touch Layer 2 (until next cron run)
- Ransomware on Layer 1 doesn't propagate to Layer 2 (until next cron run)
- We can roll back to any day in the last 30 days

---

## 3. Volume naming + mount points

Drives will be named so the role + drive type is obvious:

| Volume name | Backing device(s) | Filesystem | Mount point |
|---|---|---|---|
| `Storage-Fast-NVMe` | `/dev/disk10` (990 Pro) | APFS | `/Volumes/Storage-Fast-NVMe` |
| `Storage-Medium-NVMe` | `/dev/disk11` (Lexar 2TB) | APFS | `/Volumes/Storage-Medium-NVMe` |
| `Storage-Backup-HDD` | `/dev/disk12` + `/dev/disk13` RAID 1 | APFS | `/Volumes/Storage-Backup-HDD` |

**Naming convention**: `Storage-{speed/tier}-{drive-type}`. Speed tiers are
`Fast` (NVMe SSD, DB-suitable), `Medium` (NVMe SSD, file-suitable), `Backup`
(HDD, cold). Drive types are `NVMe` or `HDD`. Leaves room for future tiers
(e.g., `Storage-Archive-NVMe` if we add a 2nd slow SSD later).

**Why APFS for everything** (not exFAT for the RAID):
- APFS has proper journaling for RAID — better crash recovery
- Native on macOS — no driver needed
- exFAT RAID works but is more fragile on power loss
- Cross-platform recovery is a non-concern: this is a server, not a portable drive

---

## 4. Data layout (what lives where)

```
Storage-Fast-NVMe (990 Pro 1TB)        ◄── Layer 1: hot
  video_learning.db                     (SQLite, working + WAL files)
  app-cache/                            (transcode scratch, temp files)
  redis-dump/                           (LiteLLM rate-limit persistence)

Storage-Medium-NVMe (Lexar 2TB)        ◄── Layer 1: warm
  video-app/
    uploads/                            (admin-pasted YouTube metadata + caption text)
    transcripts/                        (cached YouTube captions JSON)
    generated/                          (mindmaps, summaries, quiz JSON)
    chat-history/                       (active chat messages from users)

Storage-Backup-HDD (RAID 1, 3TB)       ◄── Layer 2: cold (lagging 0-24h)
  snapshot-YYYY-MM-DD/                  (full snapshot from previous night)
    video_learning.db                   (from 990 Pro)
    video-app/uploads/                  (from Lexar)
    video-app/transcripts/
    video-app/generated/
    video-app/chat-history/
    redis-dump/
  db-backup/                            (SQLite .backup files, kept longer)
  snapshots.lock                        (prevents two cron runs at once)
```

---

## 5. Backup design

### Schedule

| Job | When | Script | Retention |
|---|---|---|---|
| **Daily snapshot** | 00:00 Asia/Singapore | `scripts/backup-daily.sh` | Keep last 30 days |
| **Monthly archive** | First of month, 00:30 SGT | `scripts/backup-monthly.sh` | Keep last 12 months |
| **Weekly verify** | Sunday, 01:00 SGT | `scripts/backup-verify.sh` | Sends summary to admin (you) |
| **DB hot backup** | Every 6 hours (00:00, 06:00, 12:00, 18:00 SGT) | `scripts/backup-db.sh` | Keep last 7 days (more granular for DB) |

**Why 00:00 SGT?** Easy to remember. Midnight Singapore time = 16:00 UTC the
previous day. Low traffic window for users (mostly Singapore-based per the
go-live plan). MBP presumed plugged in (not battery).

### Daily snapshot script (planned behavior)

```bash
#!/bin/bash
# scripts/backup-daily.sh
# Called by LaunchDaemon at 00:00 SGT every night.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
DEST="/Volumes/Storage-Backup-HDD/snapshot-${DATE}"

# Don't run if a snapshot for today already exists (idempotent)
if [ -d "$DEST" ]; then
    echo "Snapshot $DEST already exists, skipping"
    exit 0
fi

# Bail if backup drive isn't mounted (caller didn't plug it in)
if [ ! -d "/Volumes/Storage-Backup-HDD" ]; then
    echo "ERROR: Storage-Backup-HDD not mounted" >&2
    exit 1
fi

mkdir -p "$DEST"

# Use rsync with --delete to mirror exactly (no stale files in snapshots)
# Note: NOT --delete so each snapshot is a frozen point in time.
# Instead: copy into dated folder, never delete from source folder.
rsync -a /Volumes/Storage-Fast-NVMe/  "$DEST/fast/"
rsync -a /Volumes/Storage-Medium-NVMe/video-app/  "$DEST/medium/"

# SQLite-specific backup (consistent snapshot even with active writes)
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
    ".backup '$DEST/db-backup/video_learning-${DATE}.sqlite3'"

# Prune: keep only last 30 daily snapshots
cd /Volumes/Storage-Backup-HDD
ls -dt snapshot-*/ | tail -n +31 | xargs rm -rf
```

### Monthly archive script (planned behavior)

```bash
#!/bin/bash
# scripts/backup-monthly.sh
# First of every month, 00:30 SGT — creates a long-term archive.

DATE=$(date +%Y-%m)
DEST="/Volumes/Storage-Backup-HDD/monthly-${DATE}"

mkdir -p "$DEST"
# Re-rsync from Layer 1 (not from latest daily, to avoid coupling)
rsync -a /Volumes/Storage-Fast-NVMe/  "$DEST/fast/"
rsync -a /Volumes/Storage-Medium-NVMe/video-app/  "$DEST/medium/"

# Prune: keep only last 12 monthly archives
cd /Volumes/Storage-Backup-HDD
ls -dt monthly-*/ | tail -n +13 | xargs rm -rf
```

### Weekly verify script (planned behavior)

```bash
#!/bin/bash
# scripts/backup-verify.sh
# Sunday 01:00 SGT — dry-run the daily rsync to check what would change.
# If the delta is suspiciously large, email the admin.

# Count files in last snapshot vs current
LATEST=$(ls -dt /Volumes/Storage-Backup-HDD/snapshot-*/ | head -1)
NEW=$(rsync -a --dry-run --stats /Volumes/Storage-Fast-NVMe/  /tmp/ 2>&1 | grep "Number of files" | awk '{print $NF}')
echo "Last snapshot: $LATEST, would transfer $NEW files"
# (email logic stub for v1.0 — log to ~/backup-verify.log)
```

### DB hot backup (planned behavior)

Runs every 6 hours via LaunchDaemon. SQLite's `.backup` command creates a
consistent snapshot even if writes are in flight (uses SQLite's online backup
API, not file copy). More granular than the daily full snapshot because the
DB is the most critical single file.

---

## 6. Restore procedure (manual for v1.0)

If a NVMe drive dies or data gets corrupted:

1. **Identify the failure** (check `df -h`, app errors, log messages)
2. **Stop the app**: `./stop.sh` (or `launchctl unload`)
3. **Pick the snapshot to restore from**:
   ```bash
   ls -la /Volumes/Storage-Backup-HDD/
   # Pick the most recent dated folder that's BEFORE the corruption
   ```
4. **Restore**:
   ```bash
   # If NVMe drive is alive but data corrupted (rm -rf etc.):
   rsync -a /Volumes/Storage-Backup-HDD/snapshot-2026-08-21/fast/  /Volumes/Storage-Fast-NVMe/
   rsync -a /Volumes/Storage-Backup-HDD/snapshot-2026-08-21/medium/  /Volumes/Storage-Medium-NVMe/video-app/

   # If NVMe drive is dead, you must first replace the drive and reformat.
   # Then run the restore commands above.
   ```
5. **Restart app**: `./start.sh`
6. **Verify**: log in, browse a video, send a chat message

For DB-only corruption (no file damage):
```bash
cp /Volumes/Storage-Backup-HDD/db-backup/video_learning-2026-08-21.sqlite3 \
   /Volumes/Storage-Fast-NVMe/video_learning.db
```

**No fancy UI for restore in v1.0** — this is a manual ops procedure. v1.1
could add a web dashboard with restore buttons.

---

## 7. Failure modes

| What fails | What breaks | Recovery time |
|---|---|---|
| 990 Pro dies | DB unavailable, service down | Hours: replace drive, reformat APFS, restore from latest snapshot, restart |
| Lexar dies | User assets (uploads, transcripts, generated) unavailable, service degraded | Hours: same as above |
| HDD #1 dies | RAID 1 mirror degraded, **backup still works** | Days: replace HDD, rebuild RAID (`diskutil appleRAID repair`) |
| HDD #2 dies | RAID 1 mirror degraded, **backup still works** | Days: same as above |
| **Both HDDs die at once** | **Catastrophic** — backups lost | Days: replace both, restore from... nothing. (This is why we don't put valuable data only on Layer 2.) |
| Power loss during cron write | Potential RAID 1 filesystem corruption | Hours: APFS journal replays on mount; if journal also corrupted, reformat RAID and lose backups (still have live data on NVMe) |
| MBP stolen / disk dies | Lose live data + backups | Days: cloud off-site backup is the only answer (future v1.1) |

**The most likely failure (NVMe drive dying) has the longest recovery time**
because we have only 1 of each. This is acceptable for v1.0 (low user count).
When we cross 100+ active users, we add a 2nd 990 Pro + 2nd Lexar for RAID 1
on Layer 1 too.

---

## 8. Verification (how to test the design)

Once implemented, we test with these scenarios (all run manually, no automation
in v1.0):

### Test 1: Daily snapshot actually runs
```bash
# Manually trigger
./scripts/backup-daily.sh

# Verify
ls -la /Volumes/Storage-Backup-HDD/snapshot-$(date +%Y-%m-%d)/
df -h /Volumes/Storage-Backup-HDD  # check disk space grew
```

### Test 2: Snapshot is restorable
```bash
# Create a test file on Layer 1
echo "important data" > /Volumes/Storage-Fast-NVMe/test-restore.txt

# Run backup
./scripts/backup-daily.sh

# Delete it from Layer 1
rm /Volumes/Storage-Fast-NVMe/test-restore.txt

# Restore from today's snapshot
cp /Volumes/Storage-Backup-HDD/snapshot-$(date +%Y-%m-%d)/fast/test-restore.txt \
   /Volumes/Storage-Fast-NVMe/test-restore.txt

# Verify content matches
cat /Volumes/Storage-Fast-NVMe/test-restore.txt  # should say "important data"
```

### Test 3: RAID 1 tolerates a drive failure
**Don't actually unplug a drive** — this test is dangerous in production.
Instead, simulate by setting one HDD offline via diskutil:
```bash
diskutil appleRAID remove member /Volumes/Storage-Backup-HDD /dev/disk12
# Verify RAID is degraded but still mounted
ls -la /Volumes/Storage-Backup-HDD  # should still work
diskutil appleRAID list            # should show 1 of 2 members

# Re-add to repair
diskutil appleRAID repair /Volumes/Storage-Backup-HDD
# Wait for rebuild (could take hours for 3 TB)
```

### Test 4: App still works after restart with new paths
```bash
./stop.sh
# Restart with new DATABASE_URL, UPLOAD_DIR, STORAGE_DIR
./start.sh
# Log in via web UI, browse a video, send a chat
```

### Test 5: Cron actually triggers at 00:00
- Set system clock to 23:59, watch what happens at 00:00
- OR check log file: `cat ~/Library/Logs/video-app-backup.log` after midnight

---

## 9. Design principles (from earlier discussion, captured here)

These were agreed in chat 2026-08-21 and belong here for permanence:

```
1. Never hardcode paths in app code — always read from settings.upload_path /
   settings.storage_path (you already do this ✓)

2. DB path via env — already done ✓

3. Volumes named consistently — Storage-Fast-NVMe, Storage-Medium-NVMe,
   Storage-Backup-HDD (rename in Finder after mounting/RAID-ing)

4. Backups are pull-based — daily cron: rsync -a /Volumes/Storage-Fast-NVMe/
   /Volumes/Storage-Backup-HDD/snapshot-$(date +%F)/ (cheap, simple)

5. DB backup is separate — SQLite is a file, just copy it
   (sqlite3 .backup /Volumes/Storage-Backup-HDD/db-backup/...)

6. Migration path: if you ever move to a real server (e.g., Hetzner,
   DigitalOcean), all you change is the env vars — code stays identical
```

---

## 10. Open items (still to decide)

- [ ] Confirm HDD models via `smartctl -i /dev/disk12` and `/dev/disk13`
      before formatting (verify NAS-grade vs desktop-grade)
- [ ] UPS: do we have one for the MBP? (recommended for production)
- [ ] Cloud off-site backup (future v1.1): Backblaze B2 ~$6/TB/month, or
      rsync.net. Not in v1.0 scope but the architecture shouldn't block it
- [ ] Auto-mount on boot via LaunchDaemon (will be added in implementation step)

---

## Update log

- **2026-08-22** — Created doc capturing the storage layer design for MVP2
  go-live: hardware inventory, layer model (NVMe = live, HDD RAID = backup),
  backup schedule (00:00 SGT daily + monthly archive + DB hot backup every
  6h), retention policy (30 daily + 12 monthly), restore procedure, failure
  modes, verification tests.
