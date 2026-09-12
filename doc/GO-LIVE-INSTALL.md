# Go-Live Install Instructions

> **One file, one checklist, one source of truth.**
> Follow this top-to-bottom on a fresh Mac Studio (or any new prod host) and you'll have a working backup, monitoring, and public HTTPS in roughly 30 minutes.
>
> **Updated 2026-09-12** for the 9/15 launch: COOKIE_SECURE, the trial
> cohort script, the YouTube-views refresh daemon, and Firebase
> authorized-domains for the tunnel. This is the handover doc for the
> Mac Studio agent — follow Steps 0–7, then the Post-install section
> IN ORDER; the verify commands in each step are the acceptance bar.

## TL;DR (for the impatient)

```bash
# 1. Get the code
git clone https://github.com/yuanfengli168/video-learning-app.git /Users/jackyli/Code/video-learning-app
cd /Users/jackyli/Code/video-learning-app
git checkout mvp2-production-patches
git pull

# 2. Set up the venv + .env
bash scripts/setup.sh
cp .env.example .env
# → edit .env with real Firebase / Ollama / Cloudflare credentials
bash scripts/setup_firebase_key.sh   # paste service-account.json from Firebase Console

# 3. macOS System Settings (one-time, manual)
#    Privacy & Security → Full Disk Access → add:
#    /usr/bin/sqlite3
#    /bin/bash
#    /usr/bin/python3

# 4. Install the backup jobs (requires sudo)
bash scripts/install-backup-launchdaemon.sh

# 5. Install the Cloudflare Tunnel
bash scripts/install-cloudflare-tunnel.sh

# 6. Install the app server LaunchDaemon
sudo bash scripts/install-launchdaemon.sh

# 7. Verify
curl -s http://localhost:8000/api/ready | python3 -m json.tool
#    ↑ backup.is_healthy should be true
#    ↑ newest_age_hours should be < 1
curl -s https://learn.yourdomain.com/api/ready | python3 -m json.tool
```

---

## What this doc covers

| Step | Time | What you get |
|---|---|---|
| 0. Host prep | 5 min | Xcode CLI tools, homebrew, Python 3.14 |
| 1. Get the code | 1 min | Working tree on `mvp2-production-patches` |
| 2. Dependencies + secrets | 5 min | venv + `.env` + Firebase service account |
| 3. macOS permissions | 2 min | FDA for the 3 binaries that need it |
| 4. Backup jobs | 1 min | 7 launchd jobs in `/Library/LaunchDaemons/` (5 backup + prune-events + refresh-youtube-views) |
| 5. Cloudflare Tunnel | 3 min | Public HTTPS URL on your own domain |
| 6. App server | 2 min | gunicorn running, auto-start on boot |
| 7. Verify | 1 min | `/api/ready` reports healthy |

Total: ~20 min + waiting for first scheduled run.

---

## Step 0 — Host prerequisites

### 0a. Migrating from the dev MacBook Pro? (drive move, 2026-09-13 plan)

If the three external drives (Storage-Fast-NVMe, Storage-Medium-NVMe,
Storage-Backup-HDD) are coming over from the dev machine, do this on the
MacBook Pro FIRST:

```bash
# 1. Graceful stop (SQLite on a volume yanked mid-write can corrupt)
bash scripts/stop.sh
# 2. Unmount all three drives cleanly (Finder ⏏ or:)
diskutil unmount /Volumes/Storage-Fast-NVMe   # repeat for the other two
```

Then on the Mac Studio, plug in the drives and verify they mount with the
SAME volume names (the DB path, upload dir, and backup dir in .env all
reference `/Volumes/Storage-*` by name — if names differ, rename in Disk
Utility to match, or edit .env).

The DB and all uploads come over ON the drives — no data copy needed.
Three things are NOT on the drives and must be brought manually:

- `.env` — git-ignored; copy from the MacBook Pro (then set
  `COOKIE_SECURE=true` per Step 2)
- `firebase-service-account.json` — git-ignored; copy + `chmod 600`
- Ollama models — live in `~/.ollama`, NOT on the drives:
  `ollama pull glm-5.2:cloud`

### 0b. Prevent sleep (a sleeping Mac Studio = a dead site)

System Settings → Energy Saver (or Battery → Options on desktops):

- **Prevent automatic sleeping on power adapter** → ON
- **Wake for network access** → ON

(The app's own `scripts/status.sh` checks this and warns — make it green
before finishing Step 0.)

### 0c. Fresh-host prerequisites

The Mac Studio needs:

```bash
# Xcode Command Line Tools (gcc, make, etc.)
xcode-select --install

# Homebrew (for sqlite3, python3.14, cloudflared)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python 3.14 (matches the venv in the repo)
brew install python@3.14
```

Expected outcome:

| Check | Expected |
|---|---|
| `xcode-select -p` | `/Library/Developer/CommandLineTools` |
| `brew --version` | `Homebrew 4.x` |
| `python3.14 --version` | `Python 3.14.x` |

---

## Step 1 — Get the code

```bash
mkdir -p ~/Code
git clone https://github.com/yuanfengli168/video-learning-app.git ~/Code/video-learning-app
cd ~/Code/video-learning-app
git checkout mvp2-production-patches
git pull
```

Expected outcome: `git log --oneline -5` shows Day 12 commits (`1c86e8f Day12 commit 2 (probe): root-aware launchd domain + PYTHONPATH` etc.).

**Note**: the repo path matters because installers hardcode paths. We recommend `~/Code/video-learning-app/` (NOT `~/Desktop/`) because macOS TCC blocks scripts under `~/Desktop/` from launchd context. If you keep the repo elsewhere, update `RUNTIME_DIR` in `scripts/install-backup-launchdaemon.sh` and `scripts/install-launchdaemon.sh`.

---

## Step 2 — Python venv + secrets

```bash
# Create venv + install deps
bash scripts/setup.sh

# Create .env from template, then edit
cp .env.example .env
${EDITOR:-nano} .env
```

Required env vars (see `app/config.py` for full list):

```bash
# Firebase
FIREBASE_API_KEY=...
FIREBASE_PROJECT_ID=...
# (other Firebase vars — copy from your dev .env)

# Ollama (run on this same Mac Studio, or remote URL)
OLLAMA_BASE_URL=http://localhost:11434

# Cloudflare Tunnel (from Step 5)
CLOUDFLARE_TUNNEL_TOKEN=...

# Postgres (Neon or local SQLite)
DATABASE_URL=postgresql://...   # production
# OR leave unset to use SQLite at /Volumes/Storage-Fast-NVMe/video_learning.db

# ── Launch-specific (2026-09-12) ──
# Cookie Secure flag: MUST be true in production (HTTPS via the tunnel).
# Browsers refuse Secure cookies over plain http://localhost, so keep
# false only for local dev. Flip AFTER the tunnel is up (Step 5) and
# restart the app (Step 6) — login over http://localhost:8000 keeps
# working either way, but PUBLIC https logins REQUIRE secure=true.
COOKIE_SECURE=false   # ← change to true after Step 5 succeeds
```

Then add the Firebase service account:

```bash
bash scripts/setup_firebase_key.sh
# → paste the contents of firebase-service-account.json
# → the script writes it to ./firebase-service-account.json
chmod 600 firebase-service-account.json
```

Expected outcome: `cat firebase-service-account.json | head -1` shows `{` and `ls -la firebase-service-account.json` shows `-rw-------`.

---

## Step 3 — macOS permissions (manual, one-time)

System Settings → Privacy & Security → Full Disk Access → click **+** → press **`Cmd+Shift+G`** to open "Go to Folder" → enter each path below → **Go** → **Open** → toggle **ON**:

| Path | Why |
|---|---|
| `/usr/bin/sqlite3` | backup jobs use it to .backup the live DB |
| `/bin/bash` | shell that runs the backup scripts under launchd |
| `/usr/bin/python3` | the probe script runs Python under launchd |

Without these three, **all backup jobs silently fail with exit code 126** (TCC blocks sqlite3 from `/Volumes/Storage-Fast-NVMe/`). This is the single most common reason backups stop working on a new host. Verify after granting:

```bash
sudo launchctl kickstart -k system/com.videoapp.backup-db
sleep 5
sudo launchctl print system/com.videoapp.backup-db | grep "last exit"
# Expect: last exit code: 0
```

If exit code is NOT 0: open System Settings again, check the 3 toggles are all ON, then re-run.

---

## Step 4 — Backup jobs

```bash
bash scripts/install-backup-launchdaemon.sh
```

What it does:
- Unloads any user-domain LaunchAgents (`~/Library/LaunchAgents/com.videoapp.backup-*.plist`)
- Copies 5 plists from `scripts/launchdaemons/` to `/Library/LaunchDaemons/`
- Sets owner `root:wheel`, mode `644`
- Substitutes `__RUNTIME_DIR__` → `~/Library/Application Support/VideoApp/scripts/backup/`
- `sudo launchctl bootstrap system/` for each plist
- Syncs scripts to the TCC-clean runtime path

Expected outcome:

```text
✅ Synced 5 scripts
✅ Synced probe module → .../scripts/backup/probe
✅ Installed com.videoapp.backup-db
✅ Installed com.videoapp.backup-daily
✅ Installed com.videoapp.backup-monthly
✅ Installed com.videoapp.backup-verify
✅ Installed com.videoapp.backup-probe
✅ Installed com.videoapp.prune-events
✅ Installed com.videoapp.refresh-youtube-views
```

Verify:

```bash
ls -la /Library/LaunchDaemons/com.videoapp.*
sudo launchctl print system/com.videoapp.backup-probe | grep -E "state|last exit"
# Expect: state = not running, last exit code = 0 (after first run)
```

Schedule (each job runs at its time):

| Job | When | Output |
|---|---|---|
| `backup-db` | 00:00, 06:00, 12:00, 18:00 | `db-backup/video_learning-YYYY-MM-DD-HHMM.sqlite3` |
| `backup-daily` | 00:00 daily | same dir, different filename |
| `backup-monthly` | 1st of month 00:30 | `monthly-YYYY-MM/` archive |
| `backup-verify` | Sunday 01:00 | integrity check on existing backups |
| `backup-probe` | every 5 min | `/tmp/video-app-backup-status.json` |
| `prune-events` | 00:30 daily | events >90d archived to HDD, then deleted |
| `refresh-youtube-views` | 00:10 daily | `videos.view_count` snapshot for the Top Viewed tab |

---

## Step 5 — Cloudflare Tunnel (public HTTPS)

```bash
bash scripts/install-cloudflare-tunnel.sh
```

This installs `cloudflared` via brew and sets up a LaunchAgent that keeps the tunnel alive on reboot. You'll be prompted for:

1. Your Cloudflare account (login once)
2. The hostname you want (e.g. `learn.yourdomain.com`)
3. The tunnel token (from Cloudflare Zero Trust dashboard)

Expected outcome:

```text
✅ Tunnel active: https://learn.yourdomain.com
✅ Smoke test: HTTP 200 in 0.6s
```

**⚠️ REQUIRED after the tunnel is live — Firebase authorized domains.**
Google sign-in will fail with `auth/unauthorized-domain` until you add the
tunnel hostname in the Firebase console:

1. Firebase Console → Authentication → Settings → **Authorized domains**
2. Add `learn.yourdomain.com` (the tunnel hostname, exactly)
3. Verify: open `https://learn.yourdomain.com/login` in a PRIVATE window →
   the Google sign-in popup must complete without an unauthorized-domain
   error.

(Day 9 lesson: this same step was done for trycloudflare.com on the dev
host — the tunnel hostname must always be authorized or login breaks for
EVERYONE.)

**Then flip the cookie flag and restart the app:**

```bash
${EDITOR:-nano} .env    # COOKIE_SECURE=true
sudo bash scripts/restart.sh   # or: launchctl kickstart the app daemon
```

Why here: the fb_token cookie now carries the Secure flag, which browsers
only accept over HTTPS. Localhost http keeps working regardless (browsers
exempt localhost), so this flag is purely about public-URL correctness.

---

## Step 6 — App server LaunchDaemon

```bash
sudo bash scripts/install-launchdaemon.sh
```

This installs `com.video-learning-app.plist` to `/Library/LaunchDaemons/` and starts gunicorn as a system daemon. After this:

- App auto-starts on boot
- `curl http://localhost:8000/api/ready` works
- Public URL from Step 5 proxies to it

Expected outcome:

```text
✅ App running on http://localhost:8000
✅ Smoke test: 200 in 0.4s
```

---

## Step 7 — Verify

```bash
curl -s http://localhost:8000/api/ready | python3 -m json.tool
```

Expected:

```json
{
  "status": "ready",
  "db": { "status": "ok" },
  "integrity_ok": true,
  "ollama_ok": true,
  "events_table_ok": true,
  "backup": {
    "probe_present": true,
    "is_healthy": true,
    "newest_age_hours": 0.1,
    "is_stale": false
  }
}
```

If `backup.is_healthy: false`: see Step 3 (FDA missing) or wait 5 min for the probe to refresh.

Public URL check (from any device, not just the host):

```bash
curl -s https://learn.yourdomain.com/api/ready | python3 -m json.tool
# Same JSON, served through Cloudflare Tunnel
```

**Also verify the cookie carries Secure on the public URL** (after
flipping COOKIE_SECURE=true):

```bash
curl -sI https://learn.yourdomain.com/login | grep -i set-cookie
# the fb_token cookie is set at login time; to check the flag, sign in
# once via the browser and inspect the /api/auth/session response
# headers — Set-Cookie must contain: HttpOnly; Secure; SameSite=Lax
```

---

## Post-install

After the above, do these one-time things **in order**:

1. **Seed users**: `python scripts/seed_users.py` — adds the 3 known Google OAuth users with their roles (ADMIN/PAID/FREE). Idempotent.

2. **Smoke-test login**: open `https://learn.yourdomain.com/login`, sign in with one of the 3 Google accounts, verify the admin UI shows the user's role correctly.

3. **Grant the trial cohort** (soft-launch 20): collect the emails into a
   cohort file, one per line (`#` comments OK):

   ```bash
   cat > cohort.txt << 'EOF'
   # soft-launch cohort — 9/15 go-live
   friend1@gmail.com
   friend2@gmail.com
   EOF
   bash scripts/grant_trial.sh grant --all-cohort cohort.txt
   bash scripts/grant_trial.sh list   # verify all show PAID + due date
   ```

   ⚠️ Each friend must have **signed in once** before granting (user rows
   auto-create on first login — the script fails loudly with this
   explanation otherwise). At trial end (2026-10-15, 30 days):
   `bash scripts/grant_trial.sh revert --all-cohort cohort.txt`.
   Full audit trail lives in `users.notes` (granted/due/cohort/reverted).

4. **First YouTube-views refresh** (the nightly daemon runs at 00:10, but
   prime the Top Viewed tab immediately):

   ```bash
   venv/bin/python scripts/refresh_youtube_views.py
   # expect: Updated N video(s) — fills videos.view_count so the
   # dashboard's Top Viewed tab shows real numbers on day one
   ```

5. **Watch the dashboard for 24h**: open `https://learn.yourdomain.com/admin/backups` and confirm:
   - At least 4 backup files appear in `db-backup/` (one every 6 hours)
   - The probe reports `is_healthy: true` consistently
   - RAID free space is reasonable (we warn at <5 GB)

6. **Invite soft-launch users** (Day 11 of plan): 10-20 friends via Google OAuth. Share the URL in the landing page.

---

## Rollback / uninstall

If something goes wrong on prod and you need to roll back:

```bash
# Stop backups
sudo bash scripts/install-backup-launchdaemon.sh --uninstall
bash scripts/setup-backups.sh  # re-enable user-context LaunchAgents as fallback

# Stop app server
sudo bash scripts/uninstall-launchdaemon.sh

# Stop tunnel
bash scripts/install-cloudflare-tunnel.sh --uninstall
```

These scripts are designed to be idempotent and reversible. Worst case, you can `git checkout main` (a previous stable branch) and re-run Steps 4-6.

---

## What can go wrong (Day 12 lessons learned)

| Symptom | Root cause | Fix |
|---|---|---|
| All 5 backup jobs exit code 126 | TCC blocks sqlite3 from `/Volumes/Storage-Fast-NVMe/` | Step 3 — grant Full Disk Access |
| Probe says "0 backup files found" even after backup jobs succeed | Probe script needs FDA for `/bin/bash` + `/usr/bin/python3` | Step 3 |
| `backup.is_healthy: false` immediately after install | Job hasn't run yet — wait 5 min for probe refresh | Just wait |
| Backup files exist but `/api/ready` says `is_stale: true` | Newest file > 26h old | Wait for next scheduled run (or `kickstart -k`) |
| `ModuleNotFoundError: No module named 'app'` in probe log | launchd runs as root, can't readdir `~/Desktop/` | Keep repo under `~/Code/`, not `~/Desktop/` |
| App server 502 right after install | gunicorn workers still spawning | `sleep 5 && curl /api/ready` |
| Cloudflare Tunnel "no such host" | DNS not propagated yet | Wait 5 min after creating tunnel |

---

## Differences from dev environment

The Mac Studio prod is configured slightly differently from your dev MacBook Pro:

| | Dev (MacBook Pro) | Prod (Mac Studio) |
|---|---|---|
| Repo path | `~/Desktop/Githubs/video-learning-app` | `~/Code/video-learning-app` |
| Backup jobs | LaunchAgent (user) — has been broken by TCC since Aug 22 | LaunchDaemon (system) — runs as root, FDA for sqlite3/bash/python3 |
| Public URL | Cloudflare quick tunnel (`*.trycloudflare.com`) | Named tunnel with your domain |
| Database | SQLite on local SSD | SQLite on RAID-1 SSD, or Postgres on Neon |
| Ollama | runs on same machine, localhost:11434 | runs on same machine, localhost:11434 (Mac Studio has 64GB RAM) |

If you want to deploy on a different shape (Render + cloud Ollama, etc.), see `doc/deployment.md` for the cloud variant. This doc assumes the self-hosted Mac Studio path.

---

## After this doc is followed

The setup is fully reproducible on any new Mac. If you onboard a second Mac (e.g. a backup Mac Studio), follow Steps 0-7 in order. Total time: 20-30 min.

To onboard a **human collaborator** (not a Mac), give them:
- URL: `https://learn.yourdomain.com`
- Their Google account must be in the seed list (run `seed_users.py` once with their email)

That's it. No git clone, no manual FDA, no plist editing for users — they just sign in.
