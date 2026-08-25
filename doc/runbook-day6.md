# Day 6 Operator Runbook

> **Audience:** You (the operator) at 11pm when something breaks.
> **Stack:** Mac Studio + gunicorn (4 workers × 2 threads) + Ollama Pro + Cloudflare Tunnel.
> **When to read this:** When `/api/ready` is red, the public URL is broken, or the app feels slow.

---

## TL;DR — the 4 commands you'll run 95% of the time

```bash
bash scripts/status.sh    # Is the app up? What port? What does the log say?
bash scripts/restart.sh   # Stop + start (graceful)
tail -f logs/server.log   # Live tail of app access + error logs
curl http://localhost:8000/api/ready | python3 -m json.tool  # Health
```

If you don't have `scripts/restart.sh` yet, this is the manual version:

```bash
bash scripts/stop.sh && bash scripts/start.sh
```

---

## 1. Is the app up?

```bash
bash scripts/status.sh
```

Output looks like:
```
✅ gunicorn running (PID=12345)
✅ GET / → 200
✅ GET /login → 200
✅ GET /docs → 200
✅ POST .../retry-failed → 401 (auth check working)
--- Last 5 log lines ---
[2026-08-25 21:39:27 +0800] [28227] [INFO] Application startup complete.
127.0.0.1:51800 - "GET /api/health HTTP/1.1" 200
...
```

If you see `❌ gunicorn not running`, jump to section 2.
If you see `❌ GET / → 500`, jump to section 3.

---

## 2. App is down — restart it

```bash
bash scripts/stop.sh       # sends SIGTERM, waits 5s, SIGKILL
bash scripts/start.sh      # starts gunicorn with 4 workers
```

Verify with `bash scripts/status.sh` or:

```bash
curl -s http://localhost:8000/api/health
# {"status":"ok","app":"Video Learning App","server":"gunicorn"}
```

If `start.sh` fails:
1. Check the error message — usually "Ollama not running" or "venv not found"
2. Look at the very last 20 lines of `logs/server.log` to see the actual error
3. If it's a code error, the gunicorn workers will keep restarting. The
   fastest fix is `git pull && bash scripts/restart.sh` to get the latest
   code

---

## 3. App is up but 5xx errors

The smoking gun is `logs/server.log`. Common patterns:

### "OperationalError: database is locked"

SQLite is single-writer. If a long-running query (e.g. bulk transcribe)
holds a lock, every other write blocks. Fix:
- **Wait 30s** — usually the lock releases
- **Check who's holding it:**
  ```bash
  lsof video_learning.db
  ```
  You'll see the python process holding the .db file.
- **Last resort:** restart the app. SQLite releases the lock on process exit.

### "groq/compound-mini: HTTP 429"

Day 5 hotfix3: free Groq has a 250 req/day per-key cap. If you've burst
past it, all free users see 503 for ~5 min. Fix:
- **Wait** (cap resets every ~6 min for tokens, daily for requests)
- **Check usage:**
  ```bash
  curl -s http://localhost:8000/api/admin/llm/budget | python3 -m json.tool
  ```
- **Document the incident in /admin/events** for retrospective

### "Ollama connection refused"

PAID/ADMIN users can't chat or generate materials. Fix:
```bash
brew services restart ollama
# or if ollama serve was started manually:
pgrep -f "ollama serve" | xargs kill -9
ollama serve > /tmp/ollama.log 2>&1 &
```
Verify: `curl http://localhost:11434/api/tags`

### Worker keeps restarting (timeout=60s)

If a request takes >60s (e.g. yt-dlp hanging on a YouTube video), gunicorn
kills the worker. With `preload_app=True`, the master respawns it. The
UI sees a single 503; everything else is fine. Look at the log for
`CRITICAL Worker (pid: XXXX) was sent SIGKILL` to confirm.

If workers are dying every minute, check `ps aux | grep gunicorn` — if
memory is climbing, you have a leak. Restart the app and watch.

---

## 4. Public URL (Cloudflare Tunnel) is broken

The tunnel forwards `*.trycloudflare.com → localhost:8000`. Two failure modes:

### 4a. Quick tunnel (no account) is dead

```bash
# Check if the quick tunnel is running
pgrep -f "cloudflared tunnel --url" | wc -l
# 0 = died; 1 = running

# Restart it
nohup cloudflared tunnel --url http://localhost:8000 > /tmp/cf-quick.log 2>&1 &
sleep 5
grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf-quick.log | head -1
```

Note: the URL **changes on every restart** in quick mode. Share the new
URL with testers.

### 4b. Permanent tunnel is dead

```bash
# Check launchd service
sudo launchctl list | grep cloudflare
# If empty, the service isn't running

# Restart
sudo launchctl kickstart -kp system/com.cloudflare.cloudflared
sleep 5

# Check logs
tail -20 /var/log/cloudflared.log
```

If the service is **missing entirely** (reinstall after cloudflared upgrade):
```bash
sudo cloudflared service install
sudo launchctl kickstart -kp system/com.cloudflare.cloudflared
```

### 4c. Tunnel is running but returns 502/504 from Cloudflare

Cloudflare can reach the tunnel but not the upstream. Almost always
means **the local app is down**. Jump to section 2.

---

## 5. Performance investigation

The system is slow. Where's the bottleneck?

```bash
# 1. Are all 4 workers busy?
ps -p $(pgrep -f "gunicorn: worker" | head -4) -o pid,pcpu,pmem,etime
#   Look for high CPU% or > 500 MB RSS

# 2. Are requests queueing up?
#    In logs/server.log, count "200 OK" responses in last 5 min.
#    If < 30, you're starving. If 30-100, healthy.

# 3. Is the DB slow?
sqlite3 video_learning.db "PRAGMA stats;"
#   Look for "Pages" being very high (> 100k = fragmentation)

# 4. Is Ollama slow?
time ollama run glm-5.2:cloud "hello"
#   Should be < 2s for a trivial prompt. If > 10s, your
#   Ollama Pro quota might be exhausted or you're on
#   a slow network path.
```

Common fixes:
- **Restart the app** — fastest, recovers from most worker leaks
- **Clear /tmp/uvicorn.log** if it's been growing (not Day 6; legacy)
- **Vacuum the DB** (run `scripts/setup-backups.sh` which does this)

---

## 6. Disk filling up

The app writes to:
- `logs/server.log` (grows ~10 MB/day; rotated by macOS logd)
- `uploads/` (videos, ~1 GB each)
- `video_learning.db` (SQLite, ~10 MB + grows with content)
- `~/.cloudflared/` (tunnel creds, < 1 MB)

```bash
# Check disk usage
du -sh logs/ uploads/ video_learning.db
df -h /

# If disk > 90% full:
# - Run scripts/setup-backups.sh to back up + vacuum DB
# - Manually clean old uploads
find uploads/ -mtime +30 -ls
```

---

## 7. Backup and restore

Daily backups go to `~/backups/video-learning-app/<timestamp>/` (configured
in `scripts/setup-backups.sh`). The script is also installed as a
`launchd` plist that runs nightly.

Manual backup:
```bash
bash scripts/setup-backups.sh
# or just:
mkdir -p /tmp/manual-backup
sqlite3 video_learning.db ".backup /tmp/manual-backup/db.sqlite"
```

Restore from a backup:
```bash
bash scripts/stop.sh
cp ~/backups/video-learning-app/<timestamp>/video_learning.db video_learning.db
bash scripts/start.sh
```

---

## 8. When all else fails — escalation

If the runbook doesn't help:

1. **Snapshot the state:**
   ```bash
   bash scripts/status.sh > /tmp/incident-status.txt
   tail -100 logs/server.log > /tmp/incident-log.txt
   ```

2. **Restart cleanly:**
   ```bash
   bash scripts/stop.sh
   bash scripts/start.sh
   ```

3. **If the issue is in code, roll back:**
   ```bash
   git log --oneline -10
   git checkout <last-known-good-commit>
   bash scripts/restart.sh
   # Verify, then decide whether to keep the rollback or fix forward
   ```

4. **Document the incident in `/admin/events`** (admin can see live logs)

5. **Post-incident:** write a short blurb in `doc/mvp2-production-patches-status.md`
   under a new "## Incidents" section so future-you remembers.

---

## Cheat sheet — process tree

```
launchd (system)
├── com.cloudflare.cloudflared       # Cloudflare Tunnel
│   └── cloudflared                   # forwards *.trycloudflare.com → :8000
├── homebrew.mxcl.ollama              # Ollama (brew services)
│   └── ollama serve                  # LLM on :11434
└── (no launchd for our app; we use nohup + bash scripts/start.sh)
    └── gunicorn (master, proc_name=video-learning-app)
        ├── gunicorn (worker 1) ─┐
        ├── gunicorn (worker 2)  │  each runs
        ├── gunicorn (worker 3)  │  uvicorn workers
        └── gunicorn (worker 4) ─┘  serving :8000
```

---

## Last updated

2026-08-25 (Day 6)
