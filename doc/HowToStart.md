# How to start the backend

> Quick reference for the FastAPI dev server (`app/main.py`).
> **Last updated: 2026-08-19** (added Production on Mac Studio section)

This doc covers **three** contexts:

| Context | Machine | Read this section |
|---|---|---|
| Local development (interactive) | MBP | TL;DR + Option A |
| Local development (background) | MBP | Option B |
| **Production (24/7)** | **Mac Studio** | **[Production on Mac Studio](#-production-on-mac-studio)** |

---

## TL;DR (MBP development)

```bash
cd ~/Desktop/Githubs/video-learning-app
source venv/bin/activate
./scripts/start.sh
```

Then open <http://localhost:8000/login> in your browser.

---

## One-time setup (only if `venv/` is missing)

```bash
cd ~/Desktop/Githubs/video-learning-app
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./scripts/setup.sh        # creates firebase-service-account.json symlink if needed
```

> **Note on venv**: the venv is a **symlink** to `~/Desktop/video-learning-app/venv`
> (saves ~450 MB of duplicate packages). If you ever see `venv` as a broken
> symlink, just `rm venv && ln -s ~/Desktop/video-learning-app/venv venv`.

---

## Start the server

### Option A — use the helper script (foreground)

```bash
./scripts/start.sh
```

This script:
- checks Ollama is running (starts it if not)
- activates the venv
- starts **`gunicorn -c gunicorn.conf.py`** (production stack, 4 workers × 2 threads)
  in the **foreground** — you see the master's boot log and worker stderr
- tees logs to `logs/server.log` as well
- Press `Ctrl-C` to stop

> **Day 6+ note**: `start.sh` defaults to gunicorn (the same config used in
> production). For dev hot-reload with auto-restart on code changes, run
> `SERVER=uvicorn ./scripts/start.sh` instead — that re-enables uvicorn's
> `--reload` flag. See [runbook-day6.md](runbook-day6.md) for the production
> startup checklist and process model.

**Use this when developing** — gunicorn runs the same worker class as prod
(uvicorn's ASGI worker), so dev = prod for everything except `--reload`.

### Option B — manual (background, survives terminal close)

```bash
cd ~/Desktop/Githubs/video-learning-app
source venv/bin/activate
nohup gunicorn -c gunicorn.conf.py > logs/server.log 2>&1 &
echo "PID=$!"
```

The `nohup ... &` pattern keeps the server alive after the terminal closes.
Logs go to `logs/server.log`.

**Use this for demos / sharing the dev server** — it stays up until you
explicitly stop it (see below).

---

## Check it's running

```bash
./scripts/status.sh
# or
curl -s http://localhost:8000/api/health      # liveness  → {"status":"ok"}
curl -s http://localhost:8000/api/ready       # readiness → {"status":"ready","db":"ok","ollama_ok":true,...}
```

`status.sh` shows PID + port holders + smoke tests on `/`, `/login`,
`/docs`, and the auth-protected `retry-failed` endpoint.

| URL | Expected | Notes |
|---|---|---|
| `http://localhost:8000/` | `200` | Root (redirects to dashboard if logged in) |
| `http://localhost:8000/login` | `200` | Firebase login page |
| `http://localhost:8000/docs` | `200` | Auto-generated Swagger UI |
| `http://localhost:8000/healthz` | `404` | No such endpoint (don't worry, it's not missing) |
| `http://localhost:8000/api/...` (any, no auth) | `401` | "Not authenticated" — correct |

---

## Stop the server

### If you started with `start.sh` (foreground)

Press `Ctrl-C` in the terminal where it's running — gunicorn master
traps the signal and gracefully drains all 4 workers within ~30 s.

### If you started in the background (gunicorn master + 4 workers)

```bash
./scripts/stop.sh
# or, just the gunicorn master (children get reaped automatically):
pkill -f "gunicorn.*video-learning-app"
```

`stop.sh` is more robust — it tries `SIGTERM` first (gunicorn's `graceful_timeout`
drains in-flight requests), waits 5 s, escalates to `SIGKILL` if the master won't
die, and force-kills anything still holding port 8000 as a final safety net.

For a clean restart (Day 6+ recommended workflow):

```bash
./scripts/restart.sh   # stop.sh + sleep 1 + start.sh
```

---

## View logs

```bash
# Live tail
tail -f logs/server.log

# Last 50 lines
tail -50 logs/server.log

# Only errors
grep -E "ERROR|Traceback|Error" logs/server.log | tail -20
```

---

## Common errors

### "Address already in use"

```bash
lsof -ti:8000 | xargs kill -9
./scripts/start.sh
```

### "ModuleNotFoundError: No module named 'app'"

You forgot to `cd` into the project root, or you ran the command from
outside the venv. Always:

```bash
cd ~/Desktop/Githubs/video-learning-app
source venv/bin/activate
```

### "firebase-service-account.json not found"

```bash
./scripts/setup_firebase_key.sh
# or symlink it manually:
ln -sf ~/path/to/your-firebase-key.json firebase-service-account.json
```

### "Port 8000 not reachable from another device"

`gunicorn` binds to `0.0.0.0:8000` (all interfaces) by default, so the server
is reachable from your local network. Find your Mac's IP:

```bash
ipconfig getifaddr en0
# e.g. 192.168.1.42 → use http://192.168.1.42:8000
```

---

## Smoke test (no login required)

```bash
# 1. Public pages
curl -s -o /dev/null -w "GET /         → %{http_code}\n" http://localhost:8000/
curl -s -o /dev/null -w "GET /login    → %{http_code}\n" http://localhost:8000/login
curl -s -o /dev/null -w "GET /docs     → %{http_code}\n" http://localhost:8000/docs

# 2. Protected API (should 401, not 500)
curl -s -w "POST ...retry-failed → %{http_code}\n" -X POST \
  http://localhost:8000/api/courses/foo/sections/bar/retry-failed
```

All four should be `200`/`200`/`200`/`401`. If you see `500`, check
`logs/server.log` for the traceback.

---

## Run the test suite

```bash
source venv/bin/activate
python -m pytest --no-cov
```

Expected: `625 passed, 9 skipped, 0 failed` (as of 2026-08-19, after the mvp2-production-patches branch — see `doc/mvp2-production-patches-status.md`).
Coverage: ~89%.

---

## Cheat sheet

| Want to… | Run |
|---|---|
| Start server (foreground, gunicorn — same as prod) | `./scripts/start.sh` |
| Start server (foreground, dev hot-reload) | `SERVER=uvicorn ./scripts/start.sh` |
| Start server (background, survives terminal) | `source venv/bin/activate && nohup gunicorn -c gunicorn.conf.py > logs/server.log 2>&1 &` |
| Restart cleanly (Day 6+ recommended) | `./scripts/restart.sh` |
| Stop server | `./scripts/stop.sh` (or `Ctrl-C` if foreground) |
| Check it's up | `./scripts/status.sh` |
| See logs | `tail -f logs/server.log` |
| Find PID | `pgrep -f "uvicorn app.main"` |
| Kill stuck process | `lsof -ti:8000 \| xargs kill -9` |
| Run tests | `python -m pytest --no-cov` |
| Get local IP | `ipconfig getifaddr en0` |

---

## 🏭 Production on Mac Studio

> **Critical context:** Mac Studio (`Yuanfengs-Mac-Studio.local`) is the
> **production server**. It runs 24/7 via launchd. MBP is for development only.
> Don't run uvicorn manually on Mac Studio — the LaunchDaemon handles it.

### One-time setup on Mac Studio (already done if you ran the branch's TODO)

```bash
# On Mac Studio (via AnyDesk), one-time:
bash scripts/setup-env.sh                                  # fill Firebase values
sudo bash scripts/install-launchdaemon.sh                  # auto-start on boot
bash scripts/install-cloudflare-tunnel.sh --permanent      # public URL
```

### Start the server

**You don't.** The LaunchDaemon starts it on boot. If it's not running:

```bash
sudo launchctl kickstart -kp system/com.video-learning-app
```

Or:

```bash
sudo launchctl load -w /Library/LaunchDaemons/com.video-learning-app.plist
```

### Stop the server

```bash
sudo launchctl unload /Library/LaunchDaemons/com.video-learning-app.plist
# OR (less invasive — keeps plist loaded):
sudo launchctl kill TERM system/com.video-learning-app
```

### Restart the server (after deploy)

```bash
cd ~/Desktop/Githubs/video-learning-app
git pull origin main
sudo launchctl kickstart -kp system/com.video-learning-app
sleep 3
curl -s http://localhost:8000/api/health
```

### Check it's running (production checks)

```bash
# 1. Health endpoint
curl -s http://localhost:8000/api/health
# Expected: {"status":"ok","app":"Video Learning App"}

# 2. LaunchDaemon status
sudo launchctl list | grep video-learning-app
# Expected: PID + "com.video-learning-app"

# 3. Recent app logs
tail -20 ~/Library/Logs/video-learning-app.out.log

# 4. Recent errors
tail -20 ~/Library/Logs/video-learning-app.err.log

# 5. Cloudflare Tunnel status
sudo launchctl list | grep cloudflared
# Expected: PID + "com.cloudflare.cloudflared"

# 6. Disk / RAM / CPU
df -h / | tail -1
ps aux | grep uvicorn | grep -v grep | awk '{print "CPU:", $3, "MEM:", $4}'
```

### Where logs live (production)

| Log | Path |
|---|---|
| App stdout | `~/Library/Logs/video-learning-app.out.log` |
| App stderr | `~/Library/Logs/video-learning-app.err.log` |
| Cloudflare Tunnel | `/var/log/cloudflared.log` |
| Ollama | `~/.ollama/logs/` |

### Production cheat sheet (Mac Studio)

| Want to… | Run |
|---|---|
| Health check | `curl -s http://localhost:8000/api/health` |
| Restart app | `sudo launchctl kickstart -kp system/com.video-learning-app` |
| View live logs | `tail -f ~/Library/Logs/video-learning-app.out.log` |
| Pull latest code | `cd ~/Desktop/Githubs/video-learning-app && git pull origin main` |
| Check Cloudflare | `sudo launchctl list \| grep cloudflared` |
| Check sleep settings | `sudo pmset -g \| grep sleep` (must show `sleep 0`) |
| Disk space | `df -h /` |

### Disaster recovery (Mac Studio won't wake / respond)

1. **AnyDesk from MBP** → connect → if green dot but no session, see `doc/MacStudioServer/Aug082026-m2Max32gb.md` appendix
2. **SSH from same Wi-Fi**: `ssh yuanfengli@Yuanfengs-Mac-Studio.local`
3. **Hard reboot** (last resort): hold power button 10s, press once
4. **If still dead**: Time Machine restore + macOS Recovery Mode
