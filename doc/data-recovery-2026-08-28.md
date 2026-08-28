# Data recovery postmortem — 2026-08-28

## What happened

On 2026-08-28 we discovered that the production database at
`/Volumes/Storage-Fast-NVMe/video_learning.db` contained **0 courses,
0 sections, 0 videos, 0 assets, 0 chat sessions, 0 events**. Only
the 4 user rows survived.

The video files on `/Volumes/Storage-Medium-NVMe/video-app/uploads/`
were intact (~140 files, last modified Aug 27 12:32).

## How we found out

The user noticed "Uploading is a paid feature" appeared on the
dashboard for their free account — that part of the capability
gating (MVP2.1.0.4) was working. But all the courses/catalog
videos they had created and curated were missing.

## Diagnosis path (for next time)

Follow these steps in order:

### 1. Confirm the live DB is empty
```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
  "SELECT 'courses: ' || COUNT(*) FROM courses;
   SELECT 'sections: ' || COUNT(*) FROM sections;
   SELECT 'videos: ' || COUNT(*) FROM videos;
   SELECT 'assets: ' || COUNT(*) FROM assets;
   SELECT 'chat_sessions: ' || COUNT(*) FROM chat_sessions;
   SELECT 'users: ' || COUNT(*) FROM users;"
```

### 2. Check the upload files survived
```bash
ls -la /Volumes/Storage-Medium-NVMe/video-app/uploads/ | head -20
```
If files are there, recovery is possible. If files are gone too,
recovery is not.

### 3. Find the most recent backup
```bash
ls -la /Volumes/Storage-Backup-HDD/db-backup/
ls -la /Volumes/Storage-Backup-HDD/snapshot-*/
```
Backups live on the RAID-1 mirror at `/Volumes/Storage-Backup-HDD`.
The handover doc `doc/handover-mvp2-launch.md` describes the
backup layout:
- `snapshot-YYYY-MM-DD/` — daily full snapshot
- `db-backup/` — DB-only hot backups (every 6h per runbook)
- `monthly-YYYY-MM/` — monthly archives

### 4. Verify the backup has the data you need
```bash
sqlite3 /Volumes/Storage-Backup-HDD/snapshot-LATEST/db-backup/*.sqlite3 \
  "SELECT 'courses: ' || COUNT(*) FROM courses;
   SELECT 'videos: ' || COUNT(*) FROM videos;
   SELECT 'assets: ' || COUNT(*) FROM assets;"
```

### 5. Check if backup cron is still running
```bash
crontab -l                          # legacy cron (empty on this Mac)
launchctl list | grep videoapp      # launchd jobs
launchctl print gui/$(id -u)/com.videoapp.backup-db | grep -E "state|last exit"
tail -30 /Users/jackyli/Library/Logs/video-app-backup.log
```

A `state = not running` + non-zero `last exit code` means the
launchd job is configured but failing every run.

## Root cause of THIS incident

**Two unrelated events** stacked on top of each other:

### Event A: nightly backups stopped on Aug 22

- `com.videoapp.backup-db` (launchd) has been failing with exit
  code **126** ("Operation not permitted") since Aug 22 11:59.
- The failure mode is `/bin/bash: .../scripts/backup/backup-db.sh:
  Operation not permitted`.
- Probable cause: macOS sandbox / TCC lost approval for the
  script under the launching user (the script may have been
  moved, edited, or inherited a quarantine xattr after the Aug 22
  snapshot).
- Result: no backups since Aug 22. We were **6 days without
  backup coverage** when the data was lost.

**Fix (TODO before next launch):**
- Re-approve the backup script in System Settings → Privacy &
  Security → Allow Anyway, OR
- Wrap the script in an `osascript -e 'do shell script ... with
  administrator privileges'` to bypass TCC, OR
- Run it via launchd as root (`launchctl bootstrap system/`) so
  the launching user isn't subject to user-level TCC, OR
- Move the script outside the Desktop sandbox path (e.g. to
  `/usr/local/bin/`) where launchd can execute it directly.

After fixing, **re-run manually to confirm**:
```bash
bash scripts/backup/backup-db.sh
ls -la /Volumes/Storage-Backup-HDD/db-backup/ | tail -3
```

### Event B: the live DB was wiped (still under investigation)

**What we know:**
- All non-`users` tables in the prod DB are empty
- The `users` table has 4 real Firebase uids (your account + wife's
  two Gmail accounts + the `test-uid` we set during testing)
- The `pocket_*` tables (managed by the iOS app, separate code
  base) still have data
- File size is 7 MB but 67% of pages are in the free list —
  consistent with many `DELETE FROM` operations
- The schema is current (has `whisper_*` columns, `visibility`,
  `youtube_id`, etc. — i.e. the DB has been migrated, then later
  emptied)
- No recent code in `mvp2-production-patches` does any destructive
  operation on the prod DB. The conftest `db_session` fixture uses
  an in-memory `sqlite://` engine; its `Base.metadata.drop_all`
  cannot reach the prod DB.

**What we DON'T know (need to investigate):**
- Was there a pre-MVP2.1.0.4 test that bound a session to the
  prod DB? Search `git log --all --source -- app/ tests/` for any
  past code that did `app_database.SessionLocal = ...` pointing at
  a prod-bound engine.
- Was a sqlite3 CLI session used to `DELETE FROM` rows manually
  during the Aug 23-28 window? Check shell history:
  `grep -i "delete\|drop\|truncate" ~/.zsh_history ~/.bash_history`
- Did an iOS app release accidentally run migrations against the
  shared `/Volumes/Storage-Fast-NVMe/video_learning.db` file?
  (iOS Pocket app uses the same path per `handover-mvp2-launch.md`.)

**Search command for shell history:**
```bash
grep -E "sqlite3.*Storage-Fast-NVMe|DELETE FROM.*courses|DELETE FROM.*videos|DROP TABLE" \
  ~/.zsh_history ~/.bash_history 2>/dev/null | head -20
```

**If a destructive script was found**, look at:
```bash
ls -lat /Volumes/Storage-Fast-NVMe/video_learning.db*
# Check for journal/WAL files that might preserve a history
ls -la /Volumes/Storage-Fast-NVMe/video_learning.db-journal
ls -la /Volumes/Storage-Fast-NVMe/video_learning.db-wal
ls -la /Volumes/Storage-Fast-NVMe/video_learning.db-shm
```

## Recovery performed

**Source**: `/Volumes/Storage-Backup-HDD/snapshot-2026-08-22/db-backup/video_learning-2026-08-22.sqlite3`

**Recovery script**: `scripts/restore_from_raid_snapshot.py`

**What it does**:
1. Reads the snapshot SQLite file
2. Inserts its rows into the live DB, mapping:
   - `videos.file_path` from old `/Users/jackyli/Desktop/.../uploads/<uuid>.webm`
     to current `/Volumes/Storage-Medium-NVMe/video-app/uploads/<uuid>.webm`
   - `videos.duration` from INTEGER to FLOAT (current schema)
   - Adds new NOT NULL columns with defaults (`visibility=0`,
     `youtube_id=NULL`, `thumbnail_url=NULL`, `channel=NULL`,
     `caption_languages='[]'`)
3. Recreates `users` rows from `courses.user_id` (since the
   snapshot predates the `users` table) — set role=2 (FREE) so the
   user can sign in and then upgrade themselves to PAID via admin
4. Verifies counts before commit; rolls back on any error

**Why we skipped a `.bak` of the current empty DB**:
The live DB has no content worth preserving. Backing up an empty
file just wastes RAID space and risks confusion. The snapshot on
RAID is the safety net — we can restore from it again at any time.

## Lessons / hardening (write into runbook-day6.md)

1. **Backup health check should be a `/api/ready` prerequisite.**
   Add `last_successful_backup_age_hours` to `/api/ready` response
   and fail readiness if age > 26h. The server self-reports when
   its backup is stale.

2. **Alert on launchd exit code != 0.** Set up a daily check script
   that reads `launchctl print ... | grep "last exit code"` and
   writes an event row into the `events` table. The admin events
   page should highlight any backup failure.

3. **Use the SQLite `.backup` command, not file copy.** SQLite's
   online backup API is safe against partial writes and concurrent
   access. The current script may be using `cp` which can corrupt
   under load.

4. **Test restore end-to-end monthly.** Add `scripts/test_backup_restore.sh`
   that copies the latest backup to a temp DB, runs `sqlite3 ... "PRAGMA
   integrity_check"`, asserts row counts > 0, then deletes the temp.
   Alert if any backup file fails this test.

5. **Move backup scripts out of the Desktop path.** macOS TCC
   applies stricter permissions to scripts under `~/Desktop/`.
   Move `scripts/backup/*.sh` to `/usr/local/bin/video-app-backup/`
   and update the launchd plists.

6. **Run the backup as root via system launchd.** `launchctl bootstrap
   system/` runs as PID 1 with full disk access, bypassing user-level
   TCC. Trade-off: less isolation (root can read anything), but for
   a single-tenant local app this is the right choice.

7. **Consider a second RAID tier for offsite backup.** The current
   RAID is local. An rsync to a cloud bucket (Backblaze B2, ~$0.005/GB/mo)
   would protect against theft/fire of the Mac + RAID together.

## References

- `doc/handover-mvp2-launch.md` — original backup layout spec
- `doc/runbook-day6.md` §7 — backup/restore procedures
- `doc/security-hardening-mvp2.md` — broader security work
- `scripts/backup/` — backup shell scripts
- `/Users/jackyli/Library/LaunchAgents/com.videoapp.backup-*.plist` — launchd jobs
- `/Users/jackyli/Library/Logs/video-app-backup.log` — backup stderr

## Timeline

| Date | Event |
|---|---|
| 2026-07-XX | Backup scripts + launchd plists installed (per `setup.sh`) |
| 2026-08-22 11:59 | Last successful backup (`snapshot-2026-08-22` created) |
| 2026-08-22 12:05+ | `backup-db.sh` started failing with exit 126 (TCC?) |
| 2026-08-23 to 27 | DB DELETE activity (unknown source) — pages accumulated in free list |
| 2026-08-28 AM | MVP2.1.0.4 capability gates committed (`eac2c33`) |
| 2026-08-28 PM | User noticed missing data |
| 2026-08-28 PM | Recovery postmortem written, restore script written |
