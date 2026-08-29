# Prod-Must-do

> **Production host must-do checklist for the Video Learning App.**
>
> If you're setting up a new prod Mac (e.g. Mac Studio), walk through this list.
> Skipping any of these is how we ended up with silent 6-day backup gaps on 2026-08-28.

---

## 1. macOS TCC background (read this first)

**TCC** = Transparency, Consent, and Control. macOS uses it to gate file access for sandboxed apps and for some launchd jobs.

When launchd runs a script under user context (not as root), the script inherits a sandbox that **blocks `exec()` on files under `~/Desktop/`**. The failure mode is silent:

```
/bin/bash: .../backup-db.sh: Operation not permitted
```

`launchctl print` then shows `last exit code = 126` for the job. No alerts, no syslog noise — just a job that never produces output.

**What triggers it:**
- Path under `~/Desktop/` (most common — dev repos live there)
- Path under `~/Downloads/` (only if quarantine xattr is present)
- Anywhere else launchd's user-context sandbox can't reach

**What does NOT trigger it:**
- `~/Library/Application Support/...` ← use this
- `~/Library/LaunchAgents/` ← launchd reads plists from here, naturally permitted
- Anything under `/Library/...` when running as root via LaunchDaemon
- Any path in `/tmp/` (and `/private/tmp/`)

**The fix is structural, not a permissions tweak.** We do **not** strip quarantine xattrs (breaks on re-sync) and we do **not** chmod anything weird (TCC ignores POSIX bits). We move the runtime script location.

---

## 2. Repo vs runtime layout

The repo (where you develop) and the runtime (where launchd runs from) are deliberately different:

```
REPO  (source of truth, may live under ~/Desktop/ or anywhere):
  /Users/jackyli/Desktop/Githubs/video-learning-app/
    scripts/backup/
      backup-db.sh        ← repo copy, edited normally
      backup-daily.sh
      backup-monthly.sh
      backup-verify.sh
      backup-probe.sh
      com.videoapp.backup-probe.plist   ← TEMPLATE only

RUNTIME  (TCC-clean, where launchd actually invokes scripts):
  /Users/jackyli/Library/Application Support/VideoApp/scripts/backup/
    backup-db.sh          ← COPY of repo, never edited in place
    backup-daily.sh
    backup-monthly.sh
    backup-verify.sh
    backup-probe.sh

LAUNCHD PLISTS  (one per script, generated from repo templates):
  /Users/jackyli/Library/LaunchAgents/
    com.videoapp.backup-db.plist
    com.videoapp.backup-daily.plist
    com.videoapp.backup-monthly.plist
    com.videoapp.backup-verify.plist
    com.videoapp.backup-probe.plist
```

Why **copy** not **symlink**: the runtime copy is independent of the repo. You can `git pull`, `git checkout`, or move the repo around without breaking backups. The downside is that you must re-run `setup-backups.sh` after editing scripts — that's the trade-off for being structural.

---

## 3. First-time host setup

```bash
# From repo root:
cd /Users/jackyli/Desktop/Githubs/video-learning-app

# 1. Set up the 4 backup jobs (idempotent)
bash scripts/setup-backups.sh

# 2. Set up the probe (idempotent)
bash scripts/setup-probe.sh

# 3. Verify launchd sees them
launchctl list | grep videoapp
# Expect: 5 lines, last exit code = 0 for each (NOT 126!)
```

Expected output from `setup-backups.sh`:
```
✅ /Volumes/Storage-Fast-NVMe mounted
✅ /Volumes/Storage-Medium-NVMe mounted
✅ /Volumes/Storage-Backup-HDD mounted
✅ Synced 5 scripts
✅ Wrote .../com.videoapp.backup-db.plist
✅ Loaded com.videoapp.backup-db
... (×4)
```

---

## 4. After editing any backup script in the repo

Re-run the installer. It's idempotent — safe to run any number of times.

```bash
bash scripts/setup-backups.sh    # syncs scripts + reloads plists
```

If you ONLY edited `backup-probe.sh`:

```bash
bash scripts/setup-probe.sh      # syncs probe + reloads plist
```

Both scripts print what they did. If you see "✅ Synced N scripts" then `N` is correct, double-check that `launchctl print` shows `last exit code = 0` for the corresponding job.

---

## 5. Verifying the fix took hold

After install, the **first probe run completes within ~10 seconds** (RunAtLoad=true). Check it:

```bash
# 1. Probe JSON should exist and be healthy
cat /tmp/video-app-backup-status.json | python3 -m json.tool | head -20

# 2. App readiness endpoint should show backup healthy
curl -s http://localhost:8000/api/ready | python3 -m json.tool

# 3. launchctl exit code per job
for label in com.videoapp.backup-db com.videoapp.backup-daily com.videoapp.backup-monthly com.videoapp.backup-verify com.videoapp.backup-probe; do
    launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep "last exit code"
done
# Expect: 0 (not 126) for all 5
```

If `last exit code = 126`, the install path is wrong somewhere. Check:
1. The plist's `ProgramArguments` array — second string should be under `~/Library/Application Support/VideoApp/scripts/backup/`, not `~/Desktop/`
2. The script file exists and is executable: `ls -la "$HOME/Library/Application Support/VideoApp/scripts/backup/"`
3. The log at `~/Library/Logs/video-app-backup.log` for the actual `EPERM` line

---

## 6. Recovery: if backups silently stopped again

Same diagnostic as 2026-08-28 incident:

```bash
# 1. Are the jobs loaded?
launchctl list | grep videoapp

# 2. Did they last exit with 126?
launchctl print gui/$(id -u)/com.videoapp.backup-db | grep "last exit code"

# 3. What does the log say?
tail -50 ~/Library/Logs/video-app-backup.log

# 4. Is the RAID mounted?
ls /Volumes/Storage-Backup-HDD/

# 5. Is the probe telling us about it?
cat /tmp/video-app-backup-status.json | python3 -m json.tool
```

If `last exit code = 126`: re-run `scripts/setup-backups.sh` (it will copy scripts to the runtime path and reload — see step 4).

If `last exit code = 0` but no fresh backups: check the RAID, then the script log inside `backup-db.sh`.

If `last exit code = something else` (e.g. 1, 2): read the script log — the error is inside the script, not the launchd wrapper.

---

## 7. Why we don't use a LaunchDaemon (root)

We considered installing as `~/Library/LaunchDaemons/com.videoapp.backup-db.plist` which runs as root and would bypass TCC. We chose not to because:

1. **Root bypasses our permission model.** The backup script reads from `/Volumes/Storage-Fast-NVMe/video_learning.db`. Running as root means a bug in the script could corrupt or overwrite files outside the intended scope.
2. **Setup requires sudo,** which means the install can fail in user-only contexts (CI, MDM-deployed laptops, etc.).
3. **The fix is simpler in user space:** one path under `~/Library/Application Support/` and we're done.

---

## 8. Related docs

- [doc/data-recovery-2026-08-28.md](doc/data-recovery-2026-08-28.md) — postmortem for the silent backup failure that motivated this layout.
- [doc/deployment.md](doc/deployment.md) — general deployment guide (server start, env, etc.).
- [CHANGELOG.md](CHANGELOG.md) — version history; look for "Day11" entries.

---

## 9. Day-12 silent BackgroundTask failure (catalog-curation lesson)

**Symptom**: Video status stuck at `generating` forever after a fresh upload or a "Retry all failed" click. No error event in `events` table. Server is idle (workers at 0% CPU). User sees nothing in the UI except a permanently spinning progress bar.

**Root cause** — Day 12 (catalog-curation DIY test):
```python
# app/routers/courses.py:452 — retry handler
background_tasks.add_task(_run_generate_job, video_id)  # BUG: too few args

# app/routers/videos.py:681 — auto-pipeline chain
_run_generate_job(video_id)  # BUG: too few args
```

Both call sites passed **only `video_id`** to `_run_generate_job(video_id, user_id, user_role)`. The function requires **3 args**. FastAPI `BackgroundTasks` **silently swallows** the resulting `TypeError`, leaving the video stuck at `generating` and no audit-log entry to explain why.

The original test mocks (`fake_worker(video_id)`) had a matching 1-arg signature, so CI never caught it. The first real-world encounter was a DIY YouTube URL submission that succeeded for the caption-download step, then hung forever on the LLM materials step.

**The fix** (commit, see CHANGELOG.md "Day 12 catalog-curation bug"):
- `courses.py:452` — pass `user.get("uid", ""), user.get("role", 2)` (already in scope)
- `videos.py:681` — look up the owner's uid/role from `video.section.course.user_id` (the request context is gone in a BackgroundTask)
- Added regression tests that assert the worker is called with **exactly 3 args**, not `*args`
- Updated existing test stubs to match the real 3-arg signature

**Why this matters in prod**: There is no monitoring signal for "background task raised an unhandled exception." The events table is the only source of truth, but failed `BackgroundTasks` never write to it. If you ever see a video stuck at `generating` with no `events` rows newer than the initial "Starting LLM generation…" entry, **check the call-site arg count immediately**. Symptom → root cause → fix is on the order of 5 minutes if you know to look.

**Lesson for future background tasks**: any function with `user_id` / `user_role` / `request` / `db` parameters **must** be called with all of them, even if some are unused in the happy path. Add a comment at the call site stating the full arg list — the comment prevents the same bug recurring.

**Detection checklist** (run when troubleshooting a stuck video):
```bash
LIVE=/Volumes/Storage-Fast-NVMe/video_learning.db
# 1. Status stuck at 'generating'?
/usr/bin/sqlite3 "$LIVE" "SELECT id, status FROM videos WHERE status='generating';"
# 2. Any 'error' events for that video?
/usr/bin/sqlite3 "$LIVE" "SELECT ts, source, substr(message,1,100) FROM events WHERE video_id='<id>' AND level='error';"
# 3. Worker idle? (CPU should be >0% if a job is running)
/bin/ps auxww | /usr/bin/grep gunicorn | /usr/bin/grep -v grep
# If (1) is yes + (2) is empty + (3) is 0% CPU → almost certainly a silent
# BackgroundTask exception. Inspect the call site.
```
