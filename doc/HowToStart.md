# How to start the backend

> Quick reference for the FastAPI dev server (`app/main.py`).
> Last updated: 2026-07-14 (after Part A — anti-drift language policy — on commit `08c118d`).

---

## TL;DR

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
- starts `uvicorn app.main:app --reload` in the **foreground**
  (you see every request in the terminal)
- tees logs to `logs/server.log` as well
- Press `Ctrl-C` to stop

**Use this when developing** — `--reload` picks up code changes
automatically, and the inline logs make debugging easier.

### Option B — manual (background, survives terminal close)

```bash
cd ~/Desktop/Githubs/video-learning-app
source venv/bin/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs/server.log 2>&1 &
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
curl -s -o /dev/null -w "Status: %{http_code}\n" http://localhost:8000/login
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

### If you started with `start.sh`

Press `Ctrl-C` in the terminal where it's running.

### If you started in the background

```bash
./scripts/stop.sh
# or
pkill -f "uvicorn app.main"
```

`stop.sh` is more robust — it tries `SIGTERM` first, waits a second,
escalates to `SIGKILL` if the process won't die, and force-kills
anything still holding port 8000 as a final safety net.

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

`uvicorn` binds to `0.0.0.0` (all interfaces) by default, so the server
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

Expected: `487 passed, 12 pre-existing failures` (as of 2026-07-14, after Part A — anti-drift language policy).
The 12 failures are pre-existing frontend + node tests, **not** regressions from
Part A. They cover: `test_frontend.py` (3), `test_loadSummary_dom.py` (4),
`test_transcript_follow.py` (5).

---

## Cheat sheet

| Want to… | Run |
|---|---|
| Start server (foreground, auto-reload) | `./scripts/start.sh` |
| Start server (background, survives terminal) | `source venv/bin/activate && nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs/server.log 2>&1 &` |
| Stop server | `./scripts/stop.sh` (or `Ctrl-C` if foreground) |
| Check it's up | `./scripts/status.sh` |
| See logs | `tail -f logs/server.log` |
| Find PID | `pgrep -f "uvicorn app.main"` |
| Kill stuck process | `lsof -ti:8000 \| xargs kill -9` |
| Run tests | `python -m pytest --no-cov` |
| Get local IP | `ipconfig getifaddr en0` |
