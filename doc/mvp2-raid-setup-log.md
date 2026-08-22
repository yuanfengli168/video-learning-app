# MVP2 Storage Setup Log — Acasis H006 on MBP

> **Status**: Complete. This is a historical record of what was done.
> **Date**: 2026-08-22
> **Machine**: MacBook Pro (M1 Max, 32 GB RAM, macOS 15.6.1)
> **Enclosure**: Acasis H006 (Thunderbolt 3, 2× M.2 NVMe + 2× USB-SATA)

This doc records the exact steps taken to set up storage on 2026-08-22, so
that we (or anyone else) can replicate or audit the configuration later.

---

## 1. Hardware inventory at setup time

| Disk | Model | Capacity | Protocol | Role assigned |
|---|---|---|---|---|
| `/dev/disk10` | Samsung SSD 990 PRO 1TB | 1.0 TB | NVMe (Thunderbolt) | `Storage-Fast-NVMe` |
| `/dev/disk11` | Lexar SSD NM610 PRO 2TB | 2.0 TB | NVMe (Thunderbolt) | `Storage-Medium-NVMe` |
| `/dev/disk12` | 3.5" HDD (model unknown, USB-SATA bridge) | 3.0 TB | USB | RAID 1 member A |
| `/dev/disk13` | 3.5" HDD (model unknown, USB-SATA bridge) | 3.0 TB | USB | RAID 1 member B |

**HDD models not verified**: `smartctl` returns "Operation not supported by
device" on USB-attached drives (the JMicron bridge doesn't pass SMART
commands through). Verify model on Mac Studio with SATA-direct connection
OR open the enclosure and read the drive label.

---

## 2. Pre-flight verification

### Confirm drives attached

```bash
$ diskutil list
/dev/disk10 (external, physical):
   #:                       TYPE NAME                    SIZE       IDENTIFIER
   0:      GUID_partition_scheme                        *1.0 TB     disk10
/dev/disk11 (external, physical):
   0:                                                   *2.0 TB     disk11
/dev/disk12 (external, physical):
   0:                                                   *3.0 TB     disk12
/dev/disk13 (external, physical):
   0:                                                   *3.0 TB     disk13
```

### Confirm drives are unformatted (no filesystem)

```bash
$ diskutil info /dev/disk10 | grep "File System"
   File System:               None
$ diskutil info /dev/disk11 | grep "File System"
   File System:               None
$ diskutil info /dev/disk12 | grep "File System"
   File System:               None
$ diskutil info /dev/disk13 | grep "File System"
   File System:               None
```

All 4 drives had no filesystem → safe to `partitionDisk` (no data loss).

---

## 3. Format commands executed

### Step 3.1: Format NVMe #1 (Samsung 990 Pro)

```bash
diskutil partitionDisk /dev/disk10 GPT APFS Storage-Fast-NVMe 100%
```

**Result**:
```
Creating the partition map
Waiting for partitions to activate
Formatting disk10s2 as APFS with name Storage-Fast-NVMe
Mounting disk
Finished partitioning on disk10
```

**Output**:
```
/dev/disk10 (external, physical):
   0:      GUID_partition_scheme                        *1.0 TB     disk10
   1:                        EFI EFI                     209.7 MB   disk10s1
   2:                 Apple_APFS Container disk14        1000.0 GB  disk10s2
```

Mount: `/Volumes/Storage-Fast-NVMe` (931 GiB usable)

### Step 3.2: Format NVMe #2 (Lexar 2TB)

```bash
diskutil partitionDisk /dev/disk11 GPT APFS Storage-Medium-NVMe 100%
```

**Result**: APFS container `disk15s2`, mount `/Volumes/Storage-Medium-NVMe`
(1.8 TiB usable).

### Step 3.3: Create RAID 1 mirror (HDDs)

```bash
diskutil appleRAID create mirror Storage-Backup-HDD APFS /dev/disk12 /dev/disk13
```

**Result** (verbatim from terminal):
```
Unmounting proposed new member disk12
Unmounting proposed new member disk13
Repartitioning disk12 so it can be in a RAID set
Unmounting disk
Creating the partition map
Using disk12s2 as a data slice
Repartitioning disk13 so it can be in a RAID set
Unmounting disk
Creating the partition map
Using disk13s2 as a data slice
Creating a RAID set
Bringing the RAID partitions online
Waiting for the new RAID to spin up "CD72747C-1842-4296-84BA-1D332EFA7D9A"
Mounting disk
Finished RAID operation
```

**RAID set metadata**:
```
Name:                 Storage-Backup-HDD
Unique ID:            CD72747C-1842-4296-84BA-1D332EFA7D9A
Type:                 Mirror
Status:               Online
Size:                 3.0 TB (3000248991744 Bytes)
Rebuild:              manual
Device Node:          disk16
#  DevNode   UUID                                  Status     Size
0  disk12s2  14A11512-87D8-4591-88B4-D1C3AB71B6E2  Online     3000248991744
1  disk13s2  1FD24E3E-97F1-4E68-8B9F-0BB2CC277CB5  Online     3000248991744
```

Mount: `/Volumes/Storage-Backup-HDD` (2.7 TiB usable, mirror of 3TB).

### No additional macOS configuration

AppleRAID is auto-managed by macOS. The set:
- Auto-mounts when the enclosure is plugged in
- Persists across reboots
- Appears in Finder sidebar
- Shows in Disk Utility as a logical volume

**No System Settings changes required.** No fstab edits. No third-party tools.

---

## 4. Data migration

### Source (pre-migration)

```bash
$ ls -la /Users/jackyli/Desktop/Githubs/video-learning-app/
video_learning.db    6.8 MB   (SQLite, working + WAL)
uploads/             34 GB    (raw uploaded videos, pre-pivot)
storage/             (didn't exist — all assets in DB)
```

### Destination (post-migration)

```bash
mkdir -p /Volumes/Storage-Medium-NVMe/video-app
mv uploads /Volumes/Storage-Medium-NVMe/video-app/uploads
mv video_learning.db /Volumes/Storage-Fast-NVMe/video_learning.db
```

### Verification

```bash
$ ls -lh /Volumes/Storage-Fast-NVMe/video_learning.db
-rw-r--r--  1 jackyli  staff   6.8M Jul 31 14:24 video_learning.db

$ du -sh /Volumes/Storage-Medium-NVMe/video-app/uploads/
 34G    /Volumes/Storage-Medium-NVMe/video-app/uploads/

$ sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db "PRAGMA integrity_check;"
ok

$ sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
    "SELECT COUNT(*) FROM videos; SELECT COUNT(*) FROM courses;"
52
4
```

Internal drive freed: **34 GB** (uploads moved off the 96%-full system drive).

---

## 5. .env changes

```diff
- DATABASE_URL=sqlite:///./video_learning.db
+ DATABASE_URL=sqlite:////Volumes/Storage-Fast-NVMe/video_learning.db

- UPLOAD_DIR=./uploads
- STORAGE_DIR=./storage
+ UPLOAD_DIR=/Volumes/Storage-Medium-NVMe/video-app/uploads
+ STORAGE_DIR=/Volumes/Storage-Medium-NVMe/video-app/storage
+ BACKUP_DIR=/Volumes/Storage-Backup-HDD
```

`.env` (secrets, gitignored) and `.env.example` (committed) both updated.

---

## 6. Backup scripts installed

Created in `scripts/backup/`:

| Script | Schedule | Purpose |
|---|---|---|
| `backup-daily.sh` | 00:00 SGT daily | rsync NVMe → RAID, keep 30 snapshots |
| `backup-db.sh` | 00/06/12/18 SGT | SQLite `.backup`, keep 28 hot backups |
| `backup-monthly.sh` | 00:30 SGT, 1st | Long-term archive, keep 12 |
| `backup-verify.sh` | Sunday 01:00 SGT | Integrity + size check |

### LaunchDaemons loaded (per-user, MBP only)

```bash
launchctl load ~/Library/LaunchAgents/com.videoapp.backup-daily.plist
launchctl load ~/Library/LaunchAgents/com.videoapp.backup-db.plist
launchctl load ~/Library/LaunchAgents/com.videoapp.backup-monthly.plist
launchctl load ~/Library/LaunchAgents/com.videoapp.backup-verify.plist
```

**Note**: plists are in `~/Library/LaunchAgents/` (user-scoped, MBP-only).
When migrating to Mac Studio, **re-run `scripts/setup-backups.sh`** (see
handover doc).

### Test results

- `backup-daily.sh` ran: 34 GB snapshot in 278s (~120 MB/s)
- `backup-db.sh` ran: 6.8 MB backup, integrity ok
- `backup-monthly.sh` ran: 34 GB archive in 273s
- `backup-verify.sh` ran: integrity ok, file count 148

---

## 7. Bug + fix discovered during setup

**Symptom**: `rsync -a /Volumes/Storage-Fast-NVMe/ /Volumes/Storage-Backup-HDD/snapshot-.../`
exited with code 23 (partial transfer).

**Cause**: macOS Spotlight index directories (`.Spotlight-V100`, `.fseventsd`)
are unreadable from user-space, but rsync tries to recurse into them.

**Fix**: Added excludes to all rsync calls in backup scripts:
```bash
RSYNC_EXCLUDES=(
    --exclude=".Spotlight-V100"
    --exclude=".fseventsd"
    --exclude=".Trashes"
    --exclude=".TemporaryItems"
)
```

**Without excludes**: `rsync` exit code 23, daily backup script fails.
**With excludes**: clean run, no errors.

---

## 8. Final state

```
$ ls /Volumes/
Macintosh HD
ProNTFSDrive             (legacy, ignore)
Storage-Backup-HDD       (2.7 TiB, RAID 1 mirror)
Storage-Fast-NVMe        (931 GiB, APFS on 990 Pro)
Storage-Medium-NVMe      (1.8 TiB, APFS on Lexar)

$ df -h /Volumes/Storage-*
Filesystem      Size  Used  Avail  Capacity  Mounted on
/dev/disk14s1   931Gi 7.7Mi  931Gi  1%        /Volumes/Storage-Fast-NVMe
/dev/disk15s1   1.8Ti  34Gi  1.8Ti  2%        /Volumes/Storage-Medium-NVMe
/dev/disk17s1   2.7Ti  69Gi  2.7Ti  3%        /Volumes/Storage-Backup-HDD
```

Server boots, DB intact, backups running.
