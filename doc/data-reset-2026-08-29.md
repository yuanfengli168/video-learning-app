# Data Reset — 2026-08-29

> **What happened**: Restored data from the 2026-08-22 RAID snapshot on 2026-08-28, then realized the data was outdated (predated RBAC, admin-curated catalog model, YouTube integration) and reset to clean state on 2026-08-29.

## Timeline

| When | Action |
|---|---|
| 2026-08-22 (Day-7 of plan) | Last successful automated backup. Snapshot preserved in `/Volumes/Storage-Backup-HDD/snapshot-2026-08-22/` |
| 2026-08-22 → 2026-08-28 | Backup launchd jobs silently failing (exit 126 — TCC block on `~/Desktop/` scripts). No probe to alert. |
| 2026-08-28 | Discovered prod DB wiped. Wrote postmortem (`doc/data-recovery-2026-08-28.md`). Built backup health probe + admin dashboard. Pushed PR1. |
| 2026-08-28 (later) | Restored 4 courses / 6 sections / 52 videos / 666 assets from snapshot via `scripts/restore_from_snapshot.py` |
| 2026-08-29 | Reviewed restored data — too old (predates RBAC, YouTube integration, admin-curated model). All videos PRIVATE visibility (default). 3 placeholder users created as ADMIN. Outdated schema column shapes. |
| 2026-08-29 | Decided to reset to clean state. Wrote `scripts/reset_db.py` + `scripts/seed_users.py`. Reset + seeded in single session. |

## What we learned

### Snapshot was technically correct, semantically wrong

The Aug 22 snapshot predates:
- **Day-5 RBAC matrix** (ADMIN/PAID/FREE roles + capabilities)
- **Day-2 YouTube integration** (`videos.youtube_id`, `thumbnail_url`, `channel`, `caption_languages`)
- **Day-8 YouTube IFrame player** (`status='ready'` auto-computed from asset completeness)
- **`users` table** (snapshot predates user accounts)
- **`events` audit log** (snapshot predates events)

So even though we got 4 courses and 52 videos back, they had:
- `visibility = 0` (defaulted; user wanted all videos PUBLIC but couldn't tell the snapshot apart from private admin drafts)
- No `youtube_id` (can't auto-fetch captions)
- 3 placeholder users with ADMIN role whose Firebase UIDs looked real → security risk
- Asset `content` shape from before `json.loads()` was standard → some assets would crash on read

**Lesson**: a "successful restore" by row count is not the same as a "useful restore". The schema has drifted significantly in 7 days. Restoring a snapshot from a meaningfully older schema requires a migration plan, not just `INSERT INTO`.

### What to do differently next time

1. **Compare schemas first**. Before any restore, list every column in source that doesn't exist in target. Decide per column: default value? Map from equivalent? Drop?
2. **Check `users` table for orphans**. Any restored row with a `user_id` not in the live `users` table is a security risk if the Firebase UID happens to be a real account. Either drop or demote those users.
3. **Verify RBAC defaults are explicit**. Restored videos inherit `visibility = 0` (PUBLIC). That's the right default for admin-curated content but is the WRONG default for users who previously had PRIVATE videos.
4. **Decision: don't restore just because you can**. If the snapshot is from before the current schema maturity, the restore takes more time to fix than the data is worth.

## What we shipped

### Scripts (in `scripts/`)

- `restore_from_snapshot.py` — kept as emergency tool. Use only if you have a recent snapshot matching current schema.
- `reset_db.py` — wipes all data rows except `test-uid`. **Use this when starting a fresh catalog**.
- `seed_users.py` — idempotently seeds the 3 known users (jackyopenclaw.168 = PAID, jackieliglobal = ADMIN, lyf99.2022 = FREE) with correct Firebase UIDs.

### Files preserved

- `logs/reset-20260829-031148.db` — pre-reset backup. Contains the 4 courses / 52 videos / 666 assets that we just restored. Keep this for a week in case anyone wants to recover.

### State after reset (today)

| Table | Rows |
|---|---|
| users | 4 (test-uid, jackieliglobal=ADMIN, jackyopenclaw.168=PAID, lyf99.2022=FREE) |
| courses | 0 |
| sections | 0 |
| videos | 0 |
| assets | 0 |
| chat_* | 0 |
| events | 0 |
| plugin_runs | 0 |
| paid_waitlist | 0 |

Schema: **unchanged**. Tables, indexes, constraints all match Day-9.

## Backup jobs STILL broken

The probe (`/api/ready` shows `is_stale: true, newest_age_hours: 167.24`) is honest — no fresh backup has run since Aug 22. The launchd jobs cannot read the prod DB due to macOS TCC (transparency-consent-control) blocking sqlite3 from accessing `/Volumes/Storage-Fast-NVMe/` in user-context.

Fix pending (next session):
- Move to LaunchDaemon (root context) via `sudo launchctl bootstrap system/` + `/Library/LaunchDaemons/` plists
- OR grant Full Disk Access to `/bin/bash` in System Settings

## Next actions for Day 11 (security hardening)

1. Fix backup jobs (LaunchDaemon as root) — see above
2. Re-verify probe goes green
3. Start fresh catalog curation: 5 courses × 5 videos = 25 videos to source + transcribe
4. Soft-launch invite: 10-20 friends (Day 11 plan)
