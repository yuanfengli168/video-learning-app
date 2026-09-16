# MVP2 Handover — Operations Guide for New Maintainers

> **Last updated**: 2026-08-22
> **Audience**: Future you (or anyone else) who needs to run / debug / migrate this system
> **Scope**: Everything you need to know beyond the code

---

## 1. What is this app?

A read-heavy, admin-curated video learning platform. **You (admin)** paste YouTube
URLs nightly, users **watch + chat + use generated materials** (summary, mindmap,
flashcards, quiz). Built on FastAPI + SQLite + Ollama/Groq LLM, served from a Mac.

Tagline: *"I curate the best videos on the internet. You watch, learn, and chat."*

---

## 2. Server topology

| Component | Where | What |
|---|---|---|
| **App code** | `~/Desktop/Githubs/video-learning-app` | FastAPI + SQLite + LiteLLM |
| **Branch** | `mvp2-production-patches` | (was `mvp3`, renamed 2026-08-22) |
| **Machine: DEV** | MacBook Pro M1 Max **64GB** | Where development + the 2026-09-12 audit actually ran. localhost:8000 today |
| **Machine: PROD (verified 2026-09-16)** | Mac Studio 2023 (`Mac14,13`), M2 Max, **30-core GPU, 32GB RAM** | Verified on the machine itself: `system_profiler SPDisplaysDataType -json` (sppci_cores=30) + `sysctl hw.memsize hw.model`. Launch target |
| **LLM provider (free users)** | Groq cloud | `openai/gpt-oss-120b` |
| **LLM provider (admin)** | Ollama Pro cloud + local `qwen3:14b` | Paid + private |
| **Auth** | Firebase AuthKit | Email/password, Google sign-in |
| **Hosting** | Cloudflare Tunnel | Named tunnel for launch (GO-LIVE-INSTALL) |

> ⚠️ **Machine-spec history (why this table was wrong twice)**: the 9/12
> audit described a "Mac Studio 64GB" that never existed (it had audited
> the dev MacBook); the earlier table here said "MBP M1 Max 32GB" (the
> dev machine is 64GB). Every capacity number now cites the VERIFIED
> spec above. If a doc/claim conflicts with this table, this table wins
> unless re-verified on the hardware itself.

---

## 2.5 Deploying to the Mac Studio — what to expect from 2026-09-16's commits

Three launch-hardening commits landed on the dev machine and must be
verified on the Studio. They are machine-independent code (pure
Python locks, SQLite pragmas, config values) — the full test suite
covers their logic — but three things only the real deployment can
prove (GPU stack, external-volume mmap, launchd chain). Run BOTH:

### Step 1 — the suite (fast, catches logic regressions)

```
cd ~/Desktop/Githubs/video-learning-app && git pull
venv/bin/python -m pytest -q        # expect 1475 passing
```

The suite is machine-agnostic (the conftest mocks transcription
end-to-end), so a green run means the CODE behaves identically.

### Step 2 — the live-fire checklist (catches what the suite cannot)

```
bash scripts/restart.sh            # or reinstall the LaunchDaemon

# Layer 1: today's three commits, 1 minute
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db "PRAGMA journal_mode"
#   → must say: wal                      (commit eec1f36, applied on
#     first real connection; -wal/-shm files next to the DB are NORMAL
#     — do not delete them)
ps aux | grep "[g]unicorn"          # → 1 master + 4 workers
for i in 1 2 3 4 5; do curl -s -o /dev/null -w "%{time_total}\n" http://localhost:8000/api/health; done
#   → all sub-100ms, no stragglers     (threads=8 → 32 request slots)

# Layer 2: the 9/12 regression test — needs one real video upload
#   upload ONE video, then DURING transcription, every 30s:
curl http://localhost:8000/api/health
#   PASS = site stays responsive while whisper runs (9/12: it hung
#   for 4h). Watch memory in Activity Monitor too: one turbo model
#   + 1-2GB decode buffer is the expected peak, nowhere near swap.
```

If layer 1's `journal_mode` says anything other than `wal`, check
that the DB path is the file-backed one from `.env` — the pragma
listener only fires for file-backed SQLite.

### What each commit changed (ops-facing summary)

| Commit | Change | Ops impact on the Studio |
|---|---|---|
| `eec1f36` | SQLite → WAL + busy_timeout 5s + synchronous=NORMAL | Zero-migration: first connection after restart flips the mode. WAL files appear next to the DB. Reads stop stalling behind telemetry writes |
| `f605486` | model-cache threading.Lock (faster-whisper side) | None visible; MLX side intentionally untouched (subprocess architecture) |
| `821c8e4` | gunicorn threads 2 → 8 (32 slots); capacity docstring rewritten for the verified 32GB | Slightly higher per-worker memory (~64MB stacks × 4 workers) — well within budget |

---

## 3. Storage layout (the most important section to understand)

### Three external volumes (Acasis H006 enclosure)

```
/Volumes/Storage-Fast-NVMe    990 Pro 1TB (NVMe)        ─ live: DB + scratch
/Volumes/Storage-Medium-NVMe  Lexar 2TB (NVMe)          ─ live: user assets
/Volumes/Storage-Backup-HDD   2× 3TB HDD RAID 1 (USB)   ─ backup (lags by 0-24h)
```

**Key principle**: app writes to NVMes only. Backup cron copies to RAID nightly.

### What goes where

| Data | Lives at |
|---|---|
| SQLite DB + WAL | `Storage-Fast-NVMe/video_learning.db` |
| Redis dump (LiteLLM rate-limit) | `Storage-Fast-NVMe/redis-dump/` |
| App temp scratch | `Storage-Fast-NVMe/app-cache/` |
| Uploaded YouTube metadata | `Storage-Medium-NVMe/video-app/uploads/` |
| Cached YouTube captions (JSON) | `Storage-Medium-NVMe/video-app/transcripts/` |
| Generated mindmaps, summaries, quizzes | `Storage-Medium-NVMe/video-app/generated/` |
| Chat history (active) | `Storage-Medium-NVMe/video-app/chat-history/` |
| Nightly backups | `Storage-Backup-HDD/snapshot-YYYY-MM-DD/` |
| DB hot backups (every 6h) | `Storage-Backup-HDD/db-backup/` |
| Monthly archives | `Storage-Backup-HDD/monthly-YYYY-MM/` |

Full design: `doc/mvp2-storage-architecture.md`. Setup log: `doc/mvp2-raid-setup-log.md`.

---

## 4. Backup schedule (don't touch without thinking)

| When | What | Script |
|---|---|---|
| 00:00 SGT daily | Full snapshot NVMe → RAID | `backup-daily.sh` |
| 00/06/12/18 SGT | SQLite hot backup | `backup-db.sh` |
| 00:30 SGT on 1st | Monthly archive | `backup-monthly.sh` |
| Sunday 01:00 SGT | Verification + integrity | `backup-verify.sh` |

All run via LaunchDaemon. Check status:
```bash
launchctl list | grep videoapp
tail -50 ~/Library/Logs/video-app-backup.log
```

---

## 5. Roles & access control

### UserRole enum (integer in DB)

| Value | Name | Capabilities |
|---|---|---|
| 0 | ADMIN | Full access (curate, manage users, dashboard) |
| 1 | PAID | Unlimited chat, regen materials (v1.1) |
| 2 | FREE | Read-only, 30 chats/day, no upload |

### VideoVisibility enum (integer in DB)

| Value | Name | Who sees |
|---|---|---|
| 0 | PUBLIC | Anyone |
| 1 | PAID_ONLY | PAID + ADMIN |
| 2 | ADMIN_ONLY | Admin only (drafts) |

### Promote yourself to admin (use the helper script)

```bash
# After signing into the web app at least once with your admin email:
bash scripts/promote-admin.sh your@email.com
```

The script shows the full user table, validates the email exists, and confirms
the role change. Auto-detects DB path from `.env`. Full design + rationale:
`doc/mvp2-roles-and-access.md` §4.

If the helper script is unavailable (e.g., fresh Mac without scripts/),
the raw SQL equivalent is:

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db <<'EOF'
INSERT INTO users (user_id, email, role)
VALUES ('YOUR_FIREBASE_UID', 'your@email.com', 0)
ON CONFLICT(user_id) DO UPDATE SET role = 0;
EOF
```

**How to find your Firebase UID**: log into web app → browser devtools →
Application → Local Storage → look for Firebase user → copy `uid` field.
Or just list users with `bash scripts/promote-admin.sh` (no args).

### SECURITY: no self-promotion

- Role is stored server-side in SQLite (only writable via direct shell access)
- No API endpoint accepts role as input
- No client-side role gating (UI hides admin links for FREE, but server enforces)
- All SQL is parameterized (no injection vector)

---

## 6. LLM providers (free vs paid)

### Free tier (everyone default)

- **Provider**: Groq (free tier)
- **Model**: `openai/gpt-oss-120b` (primary), `openai/gpt-oss-20b` (fallback)
- **Rate limit**: 5 RPM, 30 RPD per user (via LiteLLM middleware)
- **Quota**: shared org-wide (30 RPM, 8K TPM, 1K RPD)
- **Cost**: $0

### Admin

- **Testing**: Ollama Pro `glm-5.2:cloud` ($20/mo)
- **Production**: Local `qwen3:14b` via Ollama (free, on MBP/Mac Studio)
- **Rate limit**: unlimited
- **Material generation**: Groq `qwen/qwen3.6-27b` (best JSON reliability)

### UX disclaimer (free users see this above chat)

> **Free tier** — Chats are processed by Groq (a third-party LLM provider).
> Don't share personal or sensitive information. Up to 30 chats per day.

### Caching

- Semantic cache via LiteLLM + Redis + local `bge-m3` embeddings
- Cache hit rate target: 30-50% (saves quota)
- Cache key: `hash(video_id + model + messages + temperature)`
- TTL: 7 days

### The flywheel (future v1.1+)

Every free-tier chat is logged to `chat_logs` table. Admin curates good ones.
Fine-tune Llama 3.1 8B. Host locally. Free tier becomes unlimited + private.

Full design: `doc/mvp2-llm-architecture.md`.

---

## 7. Common tasks

### Restart the app

```bash
cd ~/Desktop/Githubs/video-learning-app/scripts
./stop.sh
./start.sh
```

### Verify backups are running

```bash
launchctl list | grep videoapp
ls -la /Volumes/Storage-Backup-HDD/snapshot-$(date +%Y-%m-%d)/
```

### Manually trigger a backup

```bash
~/Desktop/Githubs/video-learning-app/scripts/backup/backup-daily.sh
```

### View app logs

```bash
tail -f ~/Desktop/Githubs/video-learning-app/logs/server.log
```

### View backup logs

```bash
tail -f ~/Library/Logs/video-app-backup.log
```

### DB inspection

```bash
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db
> .tables
> SELECT * FROM users;
> .quit
```

### Migrate to Mac Studio

```bash
# On MBP: commit + push
cd ~/Desktop/Githubs/video-learning-app
git status    # should be clean
git log -1    # confirm latest commit

# On Mac Studio:
git clone <repo-url>   # or git pull if already cloned
cd video-learning-app
git checkout mvp2-production-patches
git pull

# Plug in Acasis H006 (same enclosure, drives should auto-mount)
ls /Volumes/Storage-*

# One-time setup for backups:
bash scripts/setup-backups.sh

# Test
bash scripts/backup/backup-verify.sh

# Start app
cd scripts && ./start.sh
```

---

## 8. What to do when X breaks

### App won't start (port 8000 in use)

```bash
lsof -ti:8000 | xargs kill -9
cd ~/Desktop/Githubs/video-learning-app/scripts && ./start.sh
```

### App boots but DB errors

```bash
# Check DB integrity
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db "PRAGMA integrity_check;"

# If corrupted, restore from latest backup
cp /Volumes/Storage-Backup-HDD/db-backup/video_learning-YYYY-MM-DD-HHMM.sqlite3 \
   /Volumes/Storage-Fast-NVMe/video_learning.db

# Restart app
```

### Backup fails (drive not mounted)

```bash
ls /Volumes/
# If Storage-Backup-HDD missing: unplug + replug Acasis H006
# Check RAID status:
diskutil appleRAID list
```

### RAID degraded (1 HDD died)

```bash
diskutil appleRAID list    # shows which member is offline

# If member is missing:
# 1. Power off H006
# 2. Swap the dead HDD with a new one (same size)
# 3. Power on H006
# 4. Add new drive to RAID set:
diskutil appleRAID repair /Volumes/Storage-Backup-HDD
# Wait for rebuild (hours for 3 TB)
```

### NVMe died (data loss on live drives)

This is the bad scenario. Recovery:
1. Replace dead NVMe, format APFS, mount as same name
2. Restore from latest snapshot:
   ```bash
   rsync -a /Volumes/Storage-Backup-HDD/snapshot-LATEST_DATE/fast/ \
           /Volumes/Storage-Fast-NVMe/
   rsync -a /Volumes/Storage-Backup-HDD/snapshot-LATEST_DATE/medium/ \
           /Volumes/Storage-Medium-NVMe/video-app/
   ```
3. Restart app

You lose data between snapshot time and crash. Max loss: 24 hours
(daily backup) or 6 hours (DB hot backup).

### Both NVMes died at once

Very rare. Same as above for both. You lose 24h of data max.

### Both HDDs died at once

Catastrophic — backups lost. Recovery: replace both, reformat, lose all
backups, restore live data from... nothing. **This is the failure mode UPS
prevents.** Buy a UPS.

---

## 9. Monitoring checklist (manual, weekly)

- [ ] `launchctl list | grep videoapp` — all 4 jobs loaded
- [ ] `tail ~/Library/Logs/video-app-backup.log` — last backup succeeded
- [ ] `df -h /Volumes/Storage-*` — disk space (HDD should grow ~1-2 GB/day)
- [ ] `sqlite3 ... "PRAGMA integrity_check;"` — DB not corrupted
- [ ] `curl https://localhost:8443/api/health` — app responsive (note: HTTPS port if Cloudflare Tunnel is live)
- [ ] Visit a video page in browser — YouTube embed plays, materials load

If any of these fail, see Section 8.

---

## 10. Future work (v1.1+ roadmap)

From `doc/mvp2-final-go-live-plan.md`:

- **Stripe payments** (v1.1) — auto-promote FREE → PAID on subscription
- **Account management UI** (v1.1) — admin can change user roles via web
- **Per-user Groq keys** (v1.2) — each free user gets their own 30 RPM
- **Cloud off-site backup** (v1.1) — Backblaze B2, ~$6/TB/month
- **UPS** (v1.1) — battery backup for the Mac
- **PII scrubbing** (v1.1) — Presidio integration
- **Fine-tuned local model** (v1.2) — replaces Groq for free tier
- **Mac OS app** (v2.0) — read-only native client
- **Discord bot for YouTube picks** (v1.2) — automate admin curation
- **Datadog-grade observability** (v2.0) — replace manual checklist

---

## 11. Key files reference

| File | What |
|---|---|
| `doc/mvp2-final-go-live-plan.md` | Top-level plan, scope, 14-day schedule |
| `doc/mvp2-storage-architecture.md` | Storage design (the doc this references) |
| `doc/mvp2-raid-setup-log.md` | Exact setup commands for the RAID (this doc's companion) |
| `doc/mvp2-llm-architecture.md` | LLM provider + caching + flywheel |
| `doc/mvp2-roles-and-access.md` | Roles + visibility + promotion |
| `doc/security-hardening-mvp2.md` | Threat model + mitigations |
| `scripts/setup-backups.sh` | One-command backup setup for new machines |
| `scripts/uninstall-backups.sh` | Remove backup LaunchDaemons |
| `scripts/backup/*.sh` | The 4 backup scripts |
| `app/auth/roles.py` | UserRole + VideoVisibility enums (Day 2) |
| `app/auth/admin.py` | require_admin dependency (Day 2) |
| `.env` | Secrets (gitignored) — DB path, API keys |
| `.env.example` | Template (committed) — documentation |

---

## Update log

- **2026-08-22** — Created handover doc covering: server topology, storage
  layout, backup schedule, roles & access, LLM providers, common tasks,
  troubleshooting, monitoring checklist, future roadmap, file reference.
  Companion to `mvp2-raid-setup-log.md` (what was done) and
  `mvp2-storage-architecture.md` (why).
