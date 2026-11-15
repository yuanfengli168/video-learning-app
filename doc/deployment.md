# Deployment Guide

This guide covers deploying the Video Learning App to free-tier cloud services.

## Architecture Overview

```
User Browser
    │
    ├──> Render.com (FastAPI backend + Jinja2 templates)
    │       ├──> Neon/Supabase (PostgreSQL database)
    │       ├──> Ollama (on Oracle Cloud VM or tunnel)
    │       └──> Firebase Auth (Google + Email/Password)
    │
    └──> Firebase Auth (via AuthKit in browser)
```

**Key point:** Our frontend is Jinja2 templates rendered by the backend — there is no separate frontend to deploy. Deploying the backend = deploying the whole app.

---

## Prerequisites Checklist

- [ ] Firebase project created with Google + Email auth enabled
- [ ] `.env` filled with real Firebase config
- [ ] `firebase-service-account.json` downloaded and placed in project root
- [ ] GitHub repo pushed and up to date
- [ ] Ollama running somewhere accessible (local tunnel or remote VM)

---

## Step 1: Remote Database (Neon — Free PostgreSQL)

SQLite gets wiped on every Render deploy (ephemeral filesystem). We need a remote PostgreSQL.

1. Go to [neon.tech](https://neon.tech) → Sign up (free, GitHub login)
2. Create a new project → copy the connection string
3. It looks like: `postgresql://user:password@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`
4. Save this — you'll set it as `DATABASE_URL` on Render

> **Alternative:** [Supabase](https://supabase.com) also offers free PostgreSQL (500MB).

---

## Step 2: Deploy Backend to Render.com

1. Go to [render.com](https://render.com) → Sign in
2. **New** → **Web Service**
3. Connect your GitHub repo: `yuanfengli168/video-learning-app`
4. Configure:
   - **Name:** `video-learning-app` (or any name)
   - **Region:** Choose closest to you
   - **Branch:** `main`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
5. Add Environment Variables (see below)
6. Click **Create Web Service**
7. Wait for build + deploy (~2-3 min first time)
8. Your app will be at `https://video-learning-app.onrender.com`

### Environment Variables for Render

| Key | Value | Notes |
|-----|-------|-------|
| `APP_NAME` | `Video Learning App` | |
| `DEBUG` | `false` | |
| `DATABASE_URL` | `postgresql://...` | From Neon (Step 1) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Change to remote URL (Step 3) |
| `OLLAMA_MODEL` | `glm-5.2:cloud` | |
| `FIREBASE_API_KEY` | `AIzaSy...` | From Firebase Console |
| `FIREBASE_AUTH_DOMAIN` | `your-project.firebaseapp.com` | |
| `FIREBASE_PROJECT_ID` | `your-project-id` | |
| `FIREBASE_STORAGE_BUCKET` | `your-project.appspot.com` | |
| `FIREBASE_MESSAGING_SENDER_ID` | `123456789012` | |
| `FIREBASE_APP_ID` | `1:123456789012:web:abcdef` | |
| `FIREBASE_SERVICE_ACCOUNT_KEY_PATH` | `/etc/secrets/firebase-service-account.json` | See Step 4 below |
| `UPLOAD_DIR` | `/tmp/uploads` | Render's writable temp directory |
| `STORAGE_DIR` | `/tmp/storage` | Render's writable temp directory |

> ⚠️ **Render free tier:** Sleeps after 15 min idle, ~30s to wake up. Uploaded video files in `/tmp` will be lost on sleep/redeploy. For persistent file storage, use S3/MinIO in MVP2.

---

## Step 3: Host Ollama Remotely

Ollama can't run on Render's free tier (needs too much RAM). Options:

### Option A: Local Tunnel (Easiest for testing)
```bash
# On your Mac:
ollama serve                          # Start Ollama
npx localtunnel --port 11434          # Get a public URL like https://xxx.loca.lt
# Or use cloudflare tunnel:
cloudflared tunnel --url http://localhost:11434
```
Set `OLLAMA_BASE_URL` on Render to the tunnel URL.

> ⚠️ Tunnel must be running whenever you want to use the app. Not suitable for production.

### Option B: Oracle Cloud Free Tier (Always-on, recommended)
Oracle Cloud offers **always-free** ARM VMs (4 cores, 24GB RAM) — perfect for Ollama.

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/)
2. Create a VM instance (Ampere A1, Ubuntu 22.04)
3. SSH into the VM and install Ollama:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull glm-5.2:cloud
ollama serve  # runs on port 11434
```
4. Open port 11434 in Oracle's security list / firewall
5. Set `OLLAMA_BASE_URL` on Render to `http://YOUR_VM_IP:11434`

---

## Step 4: Firebase Service Account Key on Render

The backend needs the Firebase service account JSON to verify tokens.

1. You have `firebase-service-account.json` locally (from Firebase setup)
2. On Render, go to your web service → **Environment** tab
3. Add a **Secret File**:
   - **Filename:** `/etc/secrets/firebase-service-account.json`
   - **Content:** paste the entire JSON content of your service account key
4. Set environment variable:
   - `FIREBASE_SERVICE_ACCOUNT_KEY_PATH` = `/etc/secrets/firebase-service-account.json`

---

## Step 5: Firebase Authorized Domains

After deploying, add your Render domain to Firebase:

1. Go to [Firebase Console](https://console.firebase.google.com/) → your project
2. **Authentication** → **Settings** → **Authorized domains**
3. Click **Add domain**
4. Add: `video-learning-app.onrender.com` (your Render URL)
5. `localhost` should already be there for local dev

---

## Step 6: Test the Deployment

1. Visit `https://your-app.onrender.com` — should show the dashboard
2. Click **Sign in** — should show AuthKit login with Google + Email
3. Sign in — should redirect back to dashboard
4. Create a course → add a section → upload a video
5. Transcribe → Generate materials → try chat

> ⚠️ First visit may take ~30s (free tier wake-up). Subsequent requests are fast.

---

## Troubleshooting

### App won't start
- Check Render logs: Dashboard → your service → **Logs** tab
- Common issue: missing environment variable

### Auth not working
- Verify Firebase authorized domains include your Render URL
- Check browser console for Firebase errors
- Ensure `FIREBASE_*` env vars are correct

### Transcription fails
- Whisper `base` model needs ~1GB RAM; Render free tier has 512MB
- Fix: add `tiny` to available models, or use `tiny` as default
- Or: run transcription locally only

### Ollama connection failed
- Verify `OLLAMA_BASE_URL` is correct and accessible
- If using tunnel: ensure the tunnel is running
- If using Oracle VM: verify port 11434 is open in firewall

### Database errors
- Verify `DATABASE_URL` is the Neon/Supabase connection string
- Ensure it includes `?sslmode=require` for Neon

### Uploaded files disappear
- Render free tier has ephemeral filesystem — files in `/tmp` don't persist across deploys
- For MVP1: acceptable (re-upload after deploys)
- For MVP2: use S3/MinIO for persistent storage

---

## Cost Summary (All Free Tier)

| Service | Free Tier | Limits |
|---------|-----------|--------|
| Render.com | 1 web service | 512MB RAM, sleeps after 15 min |
| Neon (PostgreSQL) | 1 project | 0.5GB storage, 1 compute |
| Oracle Cloud (Ollama VM) | Always-free ARM | 4 cores, 24GB RAM |
| Firebase Auth | 50k auth requests/month | More than enough |
| **Total cost** | **$0** | |

---

## Local Testing (Alternative)

If remote deployment is too complex for now, you can test everything locally:

```bash
# Terminal 1: Start Ollama
ollama serve

# Terminal 2: Start the app
source venv/bin/activate
uvicorn app.main:app --reload
```

Visit `http://localhost:8000` — everything works on your machine without any deployment.

---

## Day 6 (Aug 2026): Self-Hosted on Mac Studio (Current Setup)

As of Day 6, the production deployment shifted from "Render.com + Neon PostgreSQL"
(planned in the section above) to **self-hosted on the Mac Studio running
24/7 at Yuanfengs-Mac-Studio.local**. This pivot was driven by:

- Single-user soft launch doesn't need cloud elasticity
- Mac Studio has 10 cores / 64 GB — more than enough for our workload
- One less moving piece (no DB server to maintain)
- Chat + LLM calls work better on local Ollama (no egress cost)

### Components

| Component | Process manager | Port | Logs |
|---|---|---|---|
| FastAPI app | gunicorn (4 workers × 2 threads) | 8000 | `logs/server.log` |
| Ollama | `ollama serve` (homebrew-managed) | 11434 | homebrew logs |
| Cloudflare Tunnel | `cloudflared` (launchd service) | n/a (outbound) | `/var/log/cloudflared.log` |

### Day-to-day operations

```bash
# Start everything
bash scripts/start.sh                                  # app (gunicorn)
brew services start ollama                             # Ollama (if not already)
sudo launchctl kickstart -kp system/com.cloudflare.cloudflared  # tunnel

# Health check
curl http://localhost:8000/api/health                  # liveness
curl http://localhost:8000/api/ready                   # readiness (DB + Ollama)

# Stop the app
bash scripts/stop.sh

# View logs
tail -f logs/server.log                                # app access + error
tail -f /var/log/cloudflared.log                        # tunnel

# Restart workers (e.g. after config change)
sudo kill -HUP $(cat /tmp/gunicorn.pid 2>/dev/null)    # graceful worker reload
```

### Capacity (Mac Studio: 10 cores / 64 GB RAM)

- gunicorn: 4 workers × 2 threads = 8 concurrent requests
- ~300 MB per worker = 1.2 GB total
- Soft-launch scale (10-20 users): comfortable headroom
- Bottleneck is rate limits (Day 4/5), not CPU

### Cloudflare Tunnel

The tunnel gives us a real https://*.trycloudflare.com URL without
port forwarding. Two modes:

```bash
# Quick (no account, URL changes on restart) — good for testing
bash scripts/install-cloudflare-tunnel.sh --quick

# Permanent (free Cloudflare account, fixed URL) — for soft launch
bash scripts/install-cloudflare-tunnel.sh --permanent
```

The permanent tunnel is installed as a `launchd` system service that
auto-starts on reboot. Verify with `sudo launchctl list | grep cloudflared`.

### Why not Render anymore

- Day 1-5 doc assumed Render + Neon (cloud). Mac Studio self-hosting
  is simpler and free. We can re-evaluate at scale (100+ DAU).
- One caveat: Mac Studio is a single point of failure. Offsite
  backups via `scripts/setup-backups.sh` are essential.

### Firebase Authorized Domains (Day 9 addendum)

When you expose the app to a new domain (Cloudflare Tunnel, custom
DNS, etc.), that domain must be added to Firebase's Authorized
Domains list — otherwise login fails with:

  This domain is not authorized for sign-in.
  Add localhost to the authorized domains in
  Firebase Console → Authentication → Settings.

**How to add a new domain**:

1. Open https://console.firebase.google.com/project/video-learning-app-3cf41/authentication/settings
2. Scroll to **Authorized domains**
3. Click **Add domain**
4. For Cloudflare quick tunnels: add `trycloudflare.com` (Firebase
   treats this as a wildcard prefix)
5. For permanent custom domains: add each FQDN (e.g. `app.example.com`)
6. Click **Add**

This must be done in the Firebase Console (not in our code). The
list survives across deploys — you only need to add each new domain
once. The Day 6 + Day 8 verification flow hit this gap on first
phone-test (2026-08-27) because `*.trycloudflare.com` wasn't on the
list; a 30-second Firebase Console fix unblocked the test.

If you add new deployment targets (custom domain, Render, etc.),
update this list. The default `localhost` is always there.

