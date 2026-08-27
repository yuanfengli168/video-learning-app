# MVP2 Production Patches — Status

> **Branch**: `mvp2-production-patches` (based on `main`)
> **Goal**: Make this Mac Studio (`Yuanfengs-Mac-Studio.local`) a 24/7 production server for video-learning-app.
> **Last updated**: 2026-08-27

---

## 🎯 Current state

- **Tests**: 1201 passing, 0 failing, 89% coverage
- **Branch**: ahead of `main` (pivot to admin-curated YouTube catalog)
- **Feature status**: Day 1-9 shipped (Day 10 — security hardening — next)
- **Server**: gunicorn 4 workers × 2 threads (since Day 6)

---

## ✅ Done (committed)

| # | Item | Commit |
|---|---|---|
| 1 | Branch `mvp2-production-patches` from `main` | initial |
| 2 | Installed `ffmpeg` (Homebrew 9.0.1) + fixed 3 ffmpeg tests | local |
| 3 | Fixed `tests/test_whisper_picker.py::test_transcribe_endpoint_accepts_smart_turbo_pick` | **`78a94bc`** |
| 4 | (NOBUG) Update docs to reflect Day 2A completion + new helper script | `83e2d6f` |
| 5 | **Day 1**: DB migrations + `app/auth/roles.py` (UserRole, VideoVisibility, Capability, ROLE_CAPABILITIES) + `app/auth/admin.py` (`require_capability`) + `users` table auto-create on first login | `14ce91d` + later commits |
| 6 | **Day 2A**: Admin upload form (`/admin/upload`) — URL + title + visibility dropdown (PUBLIC/PAID_ONLY/ADMIN_ONLY). `POST /api/admin/videos/youtube` creates Video row with `status='pending'`, `youtube_id` set | `5e11620` + tests `c1cad7c` |
| 7 | **Day 2A**: Catalog (`visible_videos_for_user`) excludes legacy uploads without `youtube_id` | `6507437` (NOBUG) |
| 8 | **Day 2A**: Admin upload form posts via session cookie (not `firebase.auth()` which isn't loaded on that page) | `13c607d` (NOBUG) |
| 9 | **Day 2B**: YouTube Data API v3 client (`app/services/youtube_api.py`) — `get_video_metadata`, `list_caption_tracks`, parse ISO8601 duration, pick best thumbnail. Custom exceptions | `15d357d` + tests |
| 10 | **Day 2B**: Wire YouTube API enrichment into admin POST (title, duration, thumbnail, channel, caption_languages JSON). Best-effort — API failure doesn't block admin add. `enrichment_status` field in response | `c1cad7c` + tests |
| 11 | **Day 2C Topic 1**: Catalog card polish — real YouTube thumbnail, channel name, duration overlay (m:ss format with `set` + `%02d`), course/section badge. Watch page renders YouTube iframe (`youtube-nocookie.com`) for `youtube_id` videos, falls back to `<video>` for legacy | `4e2812f` |
| 12 | **Day 2C Topic 2**: Admin upload form gets Section picker (`<select>` grouped by `<optgroup>` per Course). New `app/services/section_picker.py` (resolve_section_for_new_video with 3-tier priority: explicit → first alphabetical → auto-create). Cross-admin defense (section_id from another admin's course → 400) | `7a90c88` |
| 13 | **Day 3**: yt-dlp caption download (`app/services/youtube_captions.py` + `youtube_captions_job.py`). Replaces Whisper for auto-fire path when captions available (1-3s vs 5-15min). VTT parser handles real-world quirks. Smart retry logic — falls back without language preference if first attempt hits YouTube's 429. POST `/api/admin/videos/{id}/captions/retry` + GET `/api/admin/videos/{id}/captions/status` endpoints | `26a6bd6` |
| 14 | (NOBUG doc) `doc/public-repo-readiness.md` — 6 hardening recommendations for public GitHub repo | `833387e` |
| 15 | **Day 4 (7 commits)**: LiteLLM abstraction. Per-tier rate limits (FREE 5/min/30day, PAID 15/min/200day, ADMIN 60/min/1000day). Tier-based provider chains (FREE→[groq], PAID/ADMIN→[ollama,openai]). Ollama quota tracker (weekly 3000, 5h 800, auto-fallback at 90%). `GET /api/admin/llm/budget` endpoint + `/admin/budget` observability page | `e12f523`…`85ace1c` |
| 16 | **Day 5 (5 commits)**: Structured audit log. New `events` table (id, ts, level, source, message, user_id, video_id, context_json). `app/utils/events.py` → `log_event()` helper (never raises; mirrors to stdlib logger). Wired into `youtube_captions_job` (7 event types) and `llm_providers` (5 event types via `_audit()` short-session helper). `GET /admin/events?level=&source=&video_id=&page=` observability page with filters, badges, collapsible context | `23cdaa3`…`be44c87` || 17 | **Day 5 hotfix (4 commits)**: (a) Replace dead Groq model `llama-3.3-70b-versatile` (deprecated Aug 2026) with `groq/compound`. (b) Route chat through LiteLLM wrapper `chat_with_fallback()` — was bypassing rate-limit + audit-log + per-tier chain. (c) New `tests/test_chat_with_fallback.py` (7 tests). (d) Lower `rate_limit_free_per_day` 30 → 15 (Groq free tier is 250 req/day TOTAL per API key, not per user — math: 10 free users × 15 = 150 < 250) | `718bdbc`…`40ac3ee` |
| 18 | **Day 5 hotfix2 (5 commits)** — lyf99.2022 reported: chat input disabled on admin-curated PUBLIC videos, no transcript/materials visible. Root cause: pre-Day-1 `course.user_id == uid` ownership check inherited from the old "users upload their own video" model; never updated for admin-curated videos. Fixed 5 routes (2 chat, 1 asset-fetch, 2 transcript-fetch) + JS bug where chat input never re-enabled on error + new `app/services/video_status.py` that auto-flips `status='error'` → `'ready'` when all 5 required assets exist. New helper `user_can_access_video(role, visibility)` is the single source of truth. 30 new tests across `test_roles.py` (17) + `test_generation.py` (+2) + `test_video_status.py` (8) | `fc73f78`…`b468d39` |
| 19 | **Day 5 hotfix3 (3 commits)** — lyf99.2022 reported: chat shows `❌ [object Object]`. Root cause: `groq/compound` (router) consistently returns 413 for our 3k-token system prompts (sub-model limit too small) → `ChatCallError` → dict `detail` → JS `new Error(dict)` → `'[object Object]'`. Fixed: (a) added `formatErrorDetail()` JS helper that extracts `.message` from dict errors; (b) switched `llm_model_groq` from `groq/compound` to `groq/compound-mini` (live-tested: 200, 1.57s for video-scope chat). Tradeoff: compound-mini can 429 after a burst of free users (sub-model TPM cap) — no paid fallback per user direction, keep free tier strictly $0 | `184c095` + `4bc55d7` + (this) || 20 | **Day 6 (7 commits)** — Production server stack: (a) `gunicorn>=23.0.0` dep; (b) `gunicorn.conf.py` (4 workers × 2 threads, 60s timeout, graceful shutdown, max_requests recycling); (c) `start.sh` rewritten (default gunicorn; `SERVER=uvicorn` fallback for dev hot-reload) + `stop.sh` updated to handle both; (d) new `/api/ready` endpoint (k8s-style readiness with DB + events-table + Ollama checks, returns 503 on DB unreachable) + 5 new tests; (e) Cloudflare Tunnel verified end-to-end (live https://*.trycloudflare.com → gunicorn:8000, 200 in 0.68s) + install-cloudflare-tunnel.sh updated to check `/api/ready`; (f) `scripts/restart.sh` + `doc/runbook-day6.md` (8 sections + process tree diagram); (g) docs refresh. 5 new tests, +1 doc, +1 helper script | `61f2d89`…`5d0a0f0` (this doc) |---

## 📅 In progress (Day 9 — buffer, just shipped)

| # | Item | Plan |
|---|---|---|
| 21 | **Day 7 (done, commit `f82d216`)**: Buffer day — fixed `status.sh` not detecting gunicorn (was Day 6 regression); added Mac sleep-state check; smoke-tested `/api/health` + `/api/ready`; covered youtube_captions.py integration path with 26 mocks (49% → 95%); doc freshness refresh. | done |
| 22 | **Day 8 (done, commits `115444d`+`9e79c36`)**: YouTube IFrame API integration — `yt_player.js` wrapper for unified `<video>` and YouTube iframe control; jump-to-timestamp via `YTPlayer.seekTo()`; transcript-follow reads `currentTime` via wrapper (works for both backends); localStorage-based resume-from-last-position; 28 new tests (19 .mjs + 9 pytest wrapper). | done |
| 23 | **Day 9 (done, commit `6765fad`)**: Buffer day → real hotfix day after phone smoke test caught 3 bugs — (a) Firebase authorized domains (`trycloudflare.com` added in console, deployment.md now has the steps); (b) `SessionExpiryMiddleware` was catching all exceptions as bad cookies → split into ValueError+bounce vs FirebaseError/let-through; (c) Python worker SIGSEGV on macOS `SCDynamicStoreCopyProxiesWithOptions` (EXC_GUARD) → fixed by `export NO_PROXY=*` in start.sh. 3 new tests. Server verified end-to-end through Cloudflare Tunnel on real phone. | done |
| 18 | **Day 10 (next)**: Tier 1 security hardening (firewall, secret perms, FileVault check). Per go-live plan. | in progress |
| 19 | **Day 11-13**: Invite 10-20 friends for soft launch, bug bash + load test, polish + docs | per go-live plan |
| 20 | **Day 14**: LAUNCH | 🎯 |

---

## ❌ Not done (TODO for this branch — operational, NOT features)

| # | Item | What's needed | Who | Blocking? |
|---|---|---|---|---|
| 21 | **Login AuthKit buttons** | Create `.env` from `.env.example` and fill in the 6 Firebase fields. Restart server. | **User** | Yes — login is broken until `.env` is filled |
| 22 | **LaunchDaemon install** | Run the `sudo tee ...` block to write `/Library/LaunchDaemons/com.video-learning-app.plist` (improved version with `KeepAlive=true` + proper logs) | **User** (needs sudo) | Yes — 24/7 auto-restart won't work |
| 23 | **Reboot test** | `sudo shutdown -r now`, wait 2-3 min, reconnect via AnyDesk, run `curl http://localhost:8000/api/health` | **User** | Verifies item 22 works |
| 24 | **Cloudflare Tunnel** (NEW — for testers) | Install cloudflared, create tunnel, run as service so testers can reach the app | **User** | Yes — testers can't reach Mac Studio without this |
| 25 | **Test from phone on cellular** | Open the tunnel URL on phone (NOT wifi), verify login + add video + chat works end-to-end | **User** | Verifies item 24 works |
| 26 | **Recruit 10 free testers** | Post on LinkedIn + Twitter with the tunnel URL, ask for feedback | **User** | Goes live |
| 27 | **Cookie `Secure` flag env-driven** | Per `doc/public-repo-readiness.md` recommendation #1 (5 min) | AI (me) | No (defensive) |
| 28 | **`DB_PATH` env var override** | Per `doc/public-repo-readiness.md` recommendation #2 (15 min) | AI (me) | No (defensive) |
| 29 | **Rate-limit `/api/admin/*`** | Per `doc/public-repo-readiness.md` recommendation #3 (45 min, before launch) | AI (me) | Yes (Day 3 added outbound calls) |
| 30 | **Dependabot on GitHub** | Enable via GitHub web UI → Settings → Security → Dependabot. Free for public repos | User (2 clicks) | No |
| 31 | **gitleaks GitHub Action** | Per `doc/public-repo-readiness.md` recommendation #6 (20 min, optional) | AI (me) | No |

---

## 💡 Pricing model notes (Day 4 planning, captured 2026-08-24)

User's insight on Ollama Pro economics — a single $20/month Ollama Pro account can support ~16 concurrent PAID users at peak:

- **Per-paid-user rate limit**: 50 req / 5h, 150 req / week
- **Math**: Ollama Pro gives 800 req / 5h, 3000 req / week
- **16 paid users × 50/5h = 800/5h** (the cap)
- **16 paid users × 150/week = 2400/week** (well under 3000)
- **Tier-based chains**:
  - FREE → `[groq]` only (Groq is "free" but less powerful)
  - PAID/ADMIN → `[ollama, openai]` (Ollama = powerful default, OpenAI = paid fallback)
  - **Never** send PAID users to Groq (less powerful, wrong tier)
- **Quota tracker**: Auto-fallback from Ollama to OpenAI when 90% of weekly or 5h cap hit

This is the core business logic Day 4 implements. Future paid-tier discussions: see `doc/mvp2-final-go-live-plan.md` §"v1.1 (after launch)" for tier 2/3 ideas (currently 1 paid tier at $14.99/mo).

---

## 🚫 Not in scope for this branch (parked for separate work)

| Item | Why out of scope |
|---|---|
| Ollama LaunchDaemon / `OLLAMA_NUM_PARALLEL` tuning | Was a side-question about concurrency; should live in its own branch (e.g. `ops/ollama-launchdaemon`) |
| Singapore capacity model / GTM analysis | Discussion-only, no code |
| Pricing tiers / Stripe / payments integration | GTM, not engineering |
| iOS Pocket MVP merge (`origin/mvp-mobile-pocket-v0.1` → `main`) | Different scope; 80 files / ~17k LOC of Swift |

---

## 🧪 Test summary

```
$ ./scripts/test.sh
================= 1017 passed, 13 skipped, 561 warnings in 15.38s =================

Coverage: 89% (network code naturally hard to cover without integration tests)
```

**Branch state**:
- Local commits on `mvp2-production-patches`: **14**
- Files changed (cumulative this branch): 30+
- Pushed to origin: **Yes** — `833387e` is HEAD

---

## 📋 Recommended next steps (in order)

1. **User fills in `.env`** with real Firebase credentials → restart server → confirm login buttons appear
2. **User installs LaunchDaemon** (sudo block) → reboot → verify `curl http://localhost:8000/api/health` after reconnect
3. **User installs Cloudflare Tunnel** (`brew install cloudflared && cloudflared service install`) → verify phone access
4. **User tests end-to-end** from phone on cellular → upload a video, generate flashcards
5. **AI pushes `mvp2-production-patches`** to origin (after items 1–4 confirmed working)
6. **User recruits 10 free testers** via LinkedIn/Twitter posts
7. **Optional**: open a PR or leave on the branch

**Why Cloudflare Tunnel is in this branch (not parked)**: The whole point of `mvp2-production-patches` is "make this Mac Studio a 24/7 production server". Without public access, only the user can use it. Cloudflare Tunnel is the last step that converts "internal server" → "public beta".

---

## 🔗 Related docs

- `doc/HowToStart.md` — daily start/stop instructions
- `doc/MVP2.0-Status.md` — what shipped in MVP2.0
- `doc/MVP2.1-Status.md` — what shipped in MVP2.1 (plugin Tools tab + worker pool)
- `doc/MVP3.0-Status.md` — what's planned next
- `scripts/start.sh` — the script the LaunchDaemon will invoke on boot

---

## 🛠️ Command reference (copy-paste)

### Item 6 — Fill in `.env` with real Firebase values

```bash
# Option A: interactive wizard
bash scripts/setup-env.sh

# Option B: manual edit
open .env     # or: nano .env
# Fill in the 6 FIREBASE_* fields with values from
#   Firebase Console → ⚙️ Project settings → Your apps → Web app config
```

**Where to find each Firebase value:**

1. Go to <https://console.firebase.google.com/> → select your project
2. Click the **⚙️ gear icon** (top-left) → **Project settings**
3. Scroll to **"Your apps"** → click the `</>` Web app (or add one if missing)
4. Copy the 6 fields from the `firebaseConfig` object:

   | Firebase field | .env variable |
   |---|---|
   | `apiKey` | `FIREBASE_API_KEY` |
   | `authDomain` | `FIREBASE_AUTH_DOMAIN` |
   | `projectId` | `FIREBASE_PROJECT_ID` |
   | `storageBucket` | `FIREBASE_STORAGE_BUCKET` |
   | `messagingSenderId` | `FIREBASE_MESSAGING_SENDER_ID` |
   | `appId` | `FIREBASE_APP_ID` |

5. For the Admin SDK service account:
   - Same settings page → **Service Accounts** tab → **Generate new private key**
   - Move the downloaded JSON to `./firebase-service-account.json` via:
     ```bash
     bash scripts/setup_firebase_key.sh
     ```

**After editing `.env`, RESTART the server** (pydantic-settings only re-reads on startup):

```bash
bash scripts/stop.sh && bash scripts/start.sh
```

**Verify:**

```bash
# 1. Login page should now show the real apiKey in the HTML
curl -s http://localhost:8000/login | grep -o 'apiKey.*' | head -1
# Expected: apiKey: "AIzaSyA...", NOT "your-api-key"

# 2. Open the browser
open http://localhost:8000/login
# Expected: AuthKit widget renders Google + Email sign-in buttons that work
```

### Item 7 — Install LaunchDaemon (24/7 auto-start)

```bash
# 1. Install (one command, prompts for sudo password if needed)
sudo bash scripts/install-launchdaemon.sh

# This writes /Library/LaunchDaemons/com.video-learning-app.plist
# with KeepAlive=true (auto-restart on crash) and proper logging to
# ~/Library/Logs/video-learning-app.{out,err}.log

# 2. Verify it's running
sudo launchctl list | grep video-learning-app
# Expected: a line showing the PID + "com.video-learning-app"

# 3. Smoke test
curl http://localhost:8000/api/health
# Expected: {"status":"ok","app":"Video Learning App"}

# 4. View live logs (Ctrl-C to exit)
tail -f ~/Library/Logs/video-learning-app.out.log
```

### Item 8 — Reboot test

```bash
# 1. Reboot
sudo shutdown -r now

# 2. Wait 2-3 minutes for the Mac to come back up

# 3. Reconnect via AnyDesk

# 4. Confirm everything came back automatically
uptime                                       # should show recent boot
sudo launchctl list | grep video-learning-app  # should show PID + label
curl http://localhost:8000/api/health          # should respond with {"status":"ok",...}
```

### Item 9 — Push branch to origin

```bash
# (Done by AI on 2026-08-18 — pushed to origin/mvp2-production-patches)
git push -u origin mvp2-production-patches
```

### Bonus — Crash recovery test (without rebooting)

```bash
# Kill the app and confirm launchd restarts it within ~10 seconds
PID=$(pgrep -f "uvicorn app.main" | head -1)
echo "Killing PID=$PID"
kill -9 $PID
sleep 12
echo "New PID:"
pgrep -f "uvicorn app.main"
echo "Health:"
curl -s http://localhost:8000/api/health
```

### Uninstall (if needed)

```bash
sudo bash scripts/uninstall-launchdaemon.sh
```

### Item 10 — Install Cloudflare Tunnel (so testers can reach the app)

**Why this matters**: Mac Studio's uvicorn binds to `0.0.0.0:8000` but your home router hides it from the internet. Cloudflare Tunnel creates an outbound-only encrypted tunnel from your Mac to Cloudflare's edge, giving testers a real HTTPS URL.

**Architecture:**

```
Tester's browser
   ↓ https://<your-name>.trycloudflare.com
Cloudflare edge (Singapore PoP)
   ↓ encrypted tunnel (outbound only, no port forwarding)
Mac Studio :8000
```

**Setup (10 min):**

```bash
# 1. Install cloudflared via Homebrew (already on Mac Studio)
brew install cloudflared

# 2. Quick test — get a temporary URL (no account needed, good for initial test)
cloudflared tunnel --url http://localhost:8000
# Output: https://random-word-random.trycloudflare.com
# Copy that URL, test from phone (cellular, not wifi), then Ctrl-C

# 3. For a permanent URL, login to Cloudflare (free account)
#    Go to https://dash.cloudflare.com/sign-up if you don't have one
cloudflared tunnel login
# Browser opens, pick your account/zone (or skip if you don't have a domain)

# 4. Create a named tunnel
cloudflared tunnel create video-learning-app
# Output: Created tunnel video-learning-app with id <UUID>
# Credentials saved to ~/.cloudflared/<UUID>.json

# 5. Create config file
cat > ~/.cloudflared/config.yml << EOF
tunnel: $(ls ~/.cloudflared/*.json | head -1 | xargs basename | sed 's/.json//')
credentials-file: $(ls ~/.cloudflared/*.json | head -1)
ingress:
  - hostname: ""
    service: http://localhost:8000
  - service: http_status:404
EOF

# 6. Run tunnel as a system service (auto-start on boot)
sudo cloudflared service install
sudo launchctl kickstart -kp system/com.cloudflare.cloudflared

# 7. Verify
sudo launchctl list | grep cloudflared
# Expected: PID + com.cloudflare.cloudflared

# 8. Get your permanent URL
cloudflared tunnel info video-learning-app
# Will list your *.trycloudflare.com URL
```

**For 10 free testers**: Just use the `trycloudflare.com` subdomain. Looks like `video-learning-app.trycloudflare.com`. Works perfectly fine for testing — no need to buy a domain yet.

**When to upgrade to a real domain**: When you start charging users (after the 10 free testers validate the concept). Cost: ~$10-15/year.

### Item 11 — Test from phone on cellular (NOT wifi)

This is the **critical validation** that Cloudflare Tunnel actually works for remote users.

```bash
# 1. Make sure your Mac Studio is awake + app is running
curl -s http://localhost:8000/api/health
# Should return: {"status":"ok",...}

# 2. Get the tunnel URL (printed by cloudflared)
#    Or: cloudflared tunnel info video-learning-app

# 3. On your phone:
#    - Turn OFF wifi (use cellular data)
#    - Open browser
#    - Go to https://<your-tunnel>.trycloudflare.com
#    - Try logging in
#    - Try uploading a video

# 4. If everything works → you're ready for testers
# 5. If something fails → check /var/log/cloudflared.err.log on Mac Studio
```

### Item 12 — Recruit 10 free testers

Once the tunnel URL works, post on LinkedIn / Twitter. Suggested template:

> 🎓 I'm looking for 10 volunteers to test my new Pocket MVP — an AI-powered video learning tool that turns your course videos into flashcards + summaries.
>
> 🔗 https://video-learning-app.trycloudflare.com
> 📝 What you'll do: upload a video, generate flashcards, give me feedback (10 min survey)
> ⏱️ Free during beta, will add paid tier in ~4 weeks
>
> Built on a Mac Studio in Singapore. Honest feedback appreciated!
>
> #buildinpublic #edtech #AI

---

## 🩺 Troubleshooting

### "Buttons appear but signing in fails" or "AuthKit widget is blank/empty"

1. Check `.env` is filled in (not still placeholders):
   ```bash
   grep "^FIREBASE_API_KEY=" .env
   # If it says "your-api-key", the wizard was run with Enter-only defaults
   # → re-run `bash scripts/setup-env.sh` and paste real values
   ```

2. Verify the server is serving the real key:
   ```bash
   curl -s http://localhost:8000/login | grep -o 'apiKey.*' | head -1
   # Should print: apiKey: "AIza..."  (not "your-api-key")
   # If still placeholder, server wasn't restarted after .env edit:
   bash scripts/stop.sh && bash scripts/start.sh
   ```

3. Check Firebase console → Authentication → Sign-in method:
   - Enable **Email/Password** provider
   - Enable **Google** provider
   - Add `localhost` to **Settings → Authorized Domains**

### "LaunchDaemon says it's loaded but app isn't responding"

```bash
# Check if the process actually started
sudo launchctl list | grep video-learning-app

# Read the error log
tail -50 ~/Library/Logs/video-learning-app.err.log

# Common causes:
# - Ollama not running: `ollama serve &` or start the Ollama GUI app
# - Port 8000 already in use: `lsof -i :8000`
# - Python venv missing: `bash scripts/setup.sh`
```

### "Reboot test fails — app doesn't come back"

```bash
# 1. Is the plist still in place?
ls -la /Library/LaunchDaemons/com.video-learning-app.plist

# 2. Is it loaded?
sudo launchctl list | grep video-learning-app

# 3. If loaded but not running, what did the error log say?
tail -50 ~/Library/Logs/video-learning-app.err.log

# 4. Try unloading and reloading manually
sudo launchctl unload /Library/LaunchDaemons/com.video-learning-app.plist
sudo launchctl load -w   /Library/LaunchDaemons/com.video-learning-app.plist

# 5. If still broken, run start.sh manually to see the error directly
bash scripts/start.sh
```

---

## 📝 Update log

- **2026-08-18** — Created branch, fixed test bug (`78a94bc`), added status doc (`4206906`), added helper scripts (`a285895`), pushed to `origin/mvp2-production-patches`.


---

## 🌐 Public access — how a friend in Singapore uses the app from home

Your Mac Studio's uvicorn binds to `0.0.0.0:8000` (all interfaces), but **your home router NAT hides your Mac from the public internet**. A friend at home can't reach `192.168.x.x:8000` without one of these setups.

### The 3 options to expose your app to the internet

#### Option 1 — **Cloudflare Tunnel** (recommended for your scale)

A free Cloudflare daemon on your Mac creates an outbound tunnel to Cloudflare's edge. Cloudflare gives you a `https://your-app.trycloudflare.com` URL that proxies to your Mac.

```
Friend A's browser
   ↓ https://your-app.trycloudflare.com
Cloudflare edge (Singapore PoP)
   ↓ encrypted tunnel (outbound only — no port forwarding)
Your Mac Studio :8000
```

**Pros:** Free · Auto HTTPS · Built-in DDoS protection · Works behind any NAT · No public IP needed · Your own domain · Singapore edge (~5-20ms).

**Cons:** Cloudflare account required · Free subdomain looks like `video-learning-app.trycloudflare.com` unless you bring your own domain.

**Setup (3 commands):**
```bash
brew install cloudflared
cloudflared tunnel login      # opens browser, pick your domain
cloudflared tunnel create video-learning-app
cloudflared tunnel route dns video-learning-app your-domain.com
# Then create ~/.cloudflared/config.yml with:
#   tunnel: <TUNNEL_ID>
#   credentials-file: /Users/yuanfengli/.cloudflared/<TUNNEL_ID>.json
#   ingress:
#     - hostname: app.your-domain.com
#       service: http://localhost:8000
#     - service: http_status:404
cloudflared tunnel run video-learning-app
```

#### Option 2 — **Tailscale Funnel** (easiest, 5 min)

Tailscale is a VPN mesh. "Funnel" exposes a service from your Tailnet to the public internet via HTTPS.

**Pros:** 5-minute setup · MagicDNS stable hostname · Free for personal use (up to 100 devices) · No port forwarding.

**Cons:** URL is `https://mac-studio.YOUR-TAILNET.ts.net` (not your own domain unless paid tier) · Less DDoS protection than Cloudflare.

**Setup (2 commands):**
```bash
brew install tailscale
# Log in via browser, then:
tailscale funnel 8000
```

#### Option 3 — **Port forwarding + DDNS** (DIY, more work)

Configure your router to forward external port (e.g., 443) to your Mac's local IP. Use a Dynamic DNS service (e.g., `duckdns.org`, free) so the IP stays reachable.

**Pros:** Full control · No third-party dependency · Can use your own domain + Let's Encrypt cert.

**Cons:** Router config varies by ISP (some Singapore ISPs like StarHub block port 80/443 on residential plans) · Need to renew Let's Encrypt cert every 90 days · Home IP is exposed to internet · Singapore ISPs often give CGNAT IPs — port forwarding might not work at all.

### 📊 Which is best for you?

| Criteria | Cloudflare Tunnel | Tailscale Funnel | Port Forward + DDNS |
|---|---|---|---|
| Setup time | 10 min | 5 min | 1-2 hours |
| Cost | Free | Free | Free |
| Singapore latency | Excellent (edge PoP) | Good (single hop) | Variable |
| DDoS protection | Built-in | Basic | None |
| Your own domain | ✅ | ⚠️ (paid tier) | ✅ |
| Works behind CGNAT | ✅ | ✅ | ❌ |
| Cert management | Automatic | Automatic | Manual |
| Recommended for 100 users | ✅ **Yes** | ✅ Yes | ⚠️ Possible but risky |

**My recommendation**: **Cloudflare Tunnel**. It's industry-standard, free, handles 100 users easily, and you've already got the domain work done in the `landing-page` folder.

### Can your Mac handle 100 concurrent users?

For **read-mostly traffic** (browsing courses, watching videos, chatting):

| Resource | 100 concurrent users | Verdict |
|---|---|---|
| CPU (M2 Max) | ~5-15% utilization | ✅ Easy |
| RAM (32 GB) | ~3-8 GB used | ✅ Plenty |
| uvicorn workers | Single worker = bottleneck | ⚠️ Need 4-8 workers |
| SQLite reads | ~500-1000 queries/sec | ✅ Fine for reads |
| SQLite writes | ~50-100/sec, will lock | ⚠️ Risk under burst writes |
| Ollama (cloud) | ~$0.01-0.05 per generation | ✅ Cheap |
| **Egress bandwidth** | **~50-100 Mbps** if all 100 are watching | 🔴 **Singapore home broadband = 100-500 Mbps — tight** |

**Honest answer for 100 concurrent users:**
- 100 users browsing dashboard = ✅ Easy
- 100 users each uploading 500 MB = ❌ Impossible (uplink saturates)
- 100 users generating flashcards from transcripts = ✅ Fine (queued, Ollama cloud)
- 100 users all watching their own videos = ⚠️ Tight on bandwidth

**For 100 concurrent users you'll likely need one of:**

| Upgrade | Cost (SGD/mo) | Impact |
|---|---|---|
| Multi-worker uvicorn (gunicorn + 4 workers) | $0 | 4x request throughput |
| Cloudflare Cache for static assets | $0 | 50-70% bandwidth reduction |
| Cloudflare Stream (host videos in their CDN) | $5/1000 min watched | Offload video bandwidth entirely |
| Postgres (Supabase free tier) | $0 | Removes SQLite write lock |
| More RAM (Mac Studio 32 → 192 GB) | ~$200 one-time | More concurrent Whisper jobs |

### � 100 active → how many total paid users?

Active users ≠ paid users. Industry ratios:

| Ratio | Industry term | What it means |
|---|---|---|
| **DAU/MAU** | Daily Active / Monthly Active | Typical product: 20-30% |
| **WAU/MAU** | Weekly Active / Monthly Active | Typical product: 50-60% |
| **Paid MAU** | Paying users active in the last 30 days | Your denominator |

For a learning app (people study a few times a week, not constantly):

| If peak concurrent = | Avg DAU ≈ | Avg MAU ≈ | Conversion → paid MAU | Paid users total |
|---|---|---|---|---|
| 100 | ~300-500 | ~1,000-2,000 | 5-10% (industry for SaaS) | **5,000-20,000 total registered** |
| 50 | ~150-250 | ~500-1,000 | 5-10% | **2,500-10,000 total** |
| 25 | ~75-125 | ~250-500 | 5-10% | **1,250-5,000 total** |
| 10 | ~30-50 | ~100-200 | 5-10% | **500-2,000 total** |

**For YOUR stated goal of 1,000 paid users:**
- That implies **~10,000-20,000 total registered** (with 5-10% conversion)
- Which means **~200-400 daily active users**
- Which means **~5-15 peak concurrent users at peak** (way less than the 100 you mentioned)
- **Worst case (everyone hits "Generate Flashcards" at once): 30 concurrent**

**So your Mac Studio can comfortably handle 1,000 paid users.** The bottleneck isn't CPU/RAM/network — it's **Ollama queue depth + SQLite write locks**, both easily fixed at this scale.

### Recommended sequence for going live

1. **Now** (15 min): Install Cloudflare Tunnel → friend A can sign up from home → test with 1-2 friends
2. **Next** (30 min): Convert to gunicorn with 4 workers → handles 50 concurrent users
3. **Beta** (when you have 10 paying users): Add Postgres + Redis queue for jobs
4. **Launch** (1,000 paid users): Move video storage to Cloudflare Stream or S3 → removes bandwidth bottleneck
5. **Scale** (5,000+ paid users): Multi-region, dedicated Whisper cluster, dedicated LLM box

---

## 🔒 Security hardening — running other apps safely on this Mac

Since this Mac will host multiple apps, you want to **isolate** the video-learning-app from other workloads. Threats:

1. **A bug in another app compromises the Mac → can read your .env / Firebase service account**
2. **An attacker hits your public URL → uses it to pivot to other services**
3. **Your Mac gets stolen → secrets are in plaintext**

### Tier 1 — Already done / cheap wins (do these first)

| Action | Effort | Why |
|---|---|---|
| ✅ Firewall: enable macOS Application Firewall | 5 min | Blocks unsolicited inbound |
| ✅ Use the existing `SecurityHeadersMiddleware` (already in the app) | 0 min | Already adds CSP, X-Frame-Options, HSTS |
| ✅ `chmod 600 .env` and `firebase-service-account.json` | 1 min | Owner-only read |
| ✅ Firebase App Check | 30 min | Proves requests come from YOUR app, not a bot |
| ✅ Rate limiting (add `slowapi` middleware) | 1 hour | Prevents credential-stuffing, brute force |
| ✅ Cloudflare Tunnel (recommended above) | 10 min | Hides your home IP, DDoS protection |

### Tier 2 — Medium effort (do these before public launch)

| Action | Effort | Why |
|---|---|---|
| ⚠️ Run each app in its own Linux VM (UTM / OrbStack) | 1-2 hours | Full isolation — a bug in another app can't read this app's secrets |
| ⚠️ Use macOS sandbox-exec for the uvicorn process | 30 min | Restrict filesystem + network access at the OS level |
| ⚠️ Separate user account (e.g. `_vla` user) running the app | 30 min | Can't read your personal files even if compromised |
| ⚠️ Encrypt the disk (FileVault) | 1 min | Protects secrets if Mac is stolen |
| ⚠️ Sentry for error tracking (catches weird attacks) | 15 min | Free tier, 5K events/month |

### Tier 3 — Heavy (do when you have revenue)

| Action | Effort | Why |
|---|---|---|
| 🔒 Move to a dedicated server (not your dev Mac) | $$ | Separation of dev vs prod |
| 🔒 Vault / Doppler for secret management | $$ | Centralized, audited, rotatable secrets |
| � WAF rules (Cloudflare paid tier) | $20/mo | Blocks known attack patterns |
| 🔒 Penetration test by a 3rd party | $$ | Find holes before attackers do |

### Concrete commands for Tier 1 (run today)

```bash
# 1. Firewall on (one-shot)
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --set globalstate on
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --set blockall on

# 2. Lock down secrets
chmod 600 .env firebase-service-account.json
ls -la .env firebase-service-account.json
# Should show: -rw-------  1 yuanfengli  staff

# 3. Confirm FileVault is on (Mac encryption)
fdesetup status
# Expected: FileVault is On.

# 4. Enable Stealth Mode (don't respond to pings / probes)
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --set stealthmode on

# 5. Enable Firebase App Check (in Firebase Console)
#    → Authentication → Sign-in method → App Check → Enforce for Email/Google
#    → Requires you to register a reCAPTCHA v3 site key

# 6. Add slowapi rate limiting (optional, requires a code change)
#    Will be a separate branch if you want it
```

### "Defense in depth" architecture (recommended for 100+ users)

```
                         ┌──────────────────────────────────┐
Internet users ──────────►│  Cloudflare (DDoS + cache + WAF) │
                         └──────────────┬───────────────────�
                                        │ HTTPS (auto cert)
                                        ▼
                         ┌──────────────────────────────────┐
                         │  Cloudflare Tunnel (encrypted)   │
                         │  cloudflared daemon on your Mac  │
                         └──────────────┬───────────────────┘
                                        │ localhost:8000
                                        ▼
                         ┌──────────────────────────────────┐
                         │  macOS Application Firewall      │
                         │  (only allows inbound via tunnel)│
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────────┐
                         │  uvicorn (gunicorn 4 workers)    │
                         │  + SecurityHeadersMiddleware     │
                         │  + slowapi rate limit            │
                         │  + Sentry error tracking         │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────────┐
                         │  Firebase Auth + Admin SDK        │
                         │  + App Check enforcement         │
                         └──────────────────────────────────┘
```

Each layer is independent — a break in any one layer doesn't automatically give an attacker access to the next.

---

## 📊 Observability / dashboard — like Datadog

You want to know **which features are used, how often, by how many users** — and have alerts when things break. Here are your options, ranked by effort:

### Tier 1 — Free / minimal setup (do this first)

#### Option A — **Sentry** (errors) + custom SQLite logging (usage)

```bash
pip install sentry-sdk[fastapi]
```

In `app/main.py`:
```python
import sentry_sdk
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    traces_sample_rate=0.1,  # 10% of requests for perf
    profiles_sample_rate=0.1,
)
```

Sentry free tier: 5K errors/month, 10K performance units/month. Plenty for 1K users.

For **usage analytics**, add a simple `events` table to your SQLite:

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,     -- 'video_uploaded', 'transcribe_started', 'generate_completed', 'chat_message_sent', etc.
    metadata JSON,                -- {video_id: '...', duration_sec: 30, ...}
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_events_user ON events(user_id);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_created ON events(created_at);
```

Then a SQL query gives you "last 7 days, top features":
```sql
SELECT event_type, COUNT(*) AS n
FROM events
WHERE created_at > datetime('now', '-7 days')
GROUP BY event_type
ORDER BY n DESC;
```

#### Option B — **PostHog** (self-hosted product analytics)

- Free self-hosted (PostHog OSS) — install on your Mac with Docker
- Captures: page views, clicks, custom events, funnels, retention
- No code on the backend needed; one JS snippet in the templates

```html
<!-- Add to base.html, before </head> -->
<script>
    !function(t,e){var o,n,p;t[t[t]="Posthog"]||(e.__bo=o=e.__bo||{},
    o.capture=o.capture.bind(o),o._i=o._i||[],o.init=function(t,s){var a,c;
    a=e.createElement("script"),c=e.getElementsByTagName("script")[0],
    a.async=1,a.src=s.api_host+"/static/array.js",
    c.parentNode.insertBefore(a,c),n=o._i),o._i.push(arguments)
    }(window,document),posthog.init("phc_YOUR_KEY",{api_host:"https://us.i.posthog.com"})
</script>
```

Free cloud tier: 1M events/month, 100K session recordings.

#### Option C — **Plausible / Umami** (privacy-friendly web analytics)

- Self-hosted, lightweight (one binary)
- Only tracks page views + referrer — no user-level data
- GDPR/PDPA-friendly out of the box

**My recommendation for your scale**: **Sentry + custom `events` table** for first 100 users. Simple, no extra infra, answers both "what broke" and "what's used."

### Tier 2 — When you have 1,000+ users

| Tool | Cost | What it gives you |
|---|---|---|
| **Datadog** | $15/host/month | Full APM, logs, metrics, alerts |
| **New Relic** | Free tier 100GB/month | APM + browser + mobile |
| **Grafana + Prometheus** | Free (self-hosted) | Dashboards + alerting, ~30 min setup |
| **Better Stack** | $20/mo | Logs + uptime + on-call |
| **Highlight.io** | Free tier | Session replay + error tracking |

For your budget + Mac Studio, I'd skip Datadog (overkill) and use:
- **Sentry** for errors
- **SQLite `events` table** for usage (queries are fast, no extra cost)
- **Uptime Kuma** (free, self-hosted) for uptime monitoring — runs as a tiny Docker container

### Tier 3 — When you have revenue

- **Datadog or New Relic** for APM (Application Performance Monitoring)
- **PostHog Cloud** for product analytics + feature flags
- **PagerDuty / Opsgenie** for on-call alerts

---

## 🏗️ Production architecture (Aug 19, 2026)

### Goal

Separate **development** from **production** so that:
- MBP is for writing code, running tests, debugging
- Mac Studio is for **only** serving users (24/7)
- GitHub is the source of truth + runs CI/CD
- Local edits on Mac Studio are **forbidden** (it should only `git pull`)

### Three-machine workflow

```
┌──────────────────────┐         ┌──────────────────┐         ┌──────────────────────────┐
│  MBP (dev/tests)     │         │  GitHub          │         │  Mac Studio (prod)       │
│  Sequoia 15.6.1      │  push   │  source of truth │  pull   │  Sequoia 15.7.9         │
│  M1 Max / 64GB       │ ──────► │  CI/CD (future)  │ ──────► │  M2 Max / 32GB / 512GB  │
│                      │         │                  │         │  Yuanfengs-Mac-Studio   │
└──────────────────────┘         └──────────────────┘         └──────────────────────────┘
        ▲                                                                  │
        │                                                                  │
        └──────────────── AnyDesk (cross-network) ────────────────────────┘
                            SSH (same network only)
```

### Roles

| Machine | Role | Allowed actions | Forbidden actions |
|---|---|---|---|
| **MBP** | Developer workstation | Edit code, run tests, commit, push, create PRs | None (this is the dev machine) |
| **GitHub** | Source of truth + CI | Run tests on push, host main + feature branches | n/a |
| **Mac Studio** | Production server | `git pull`, restart app, monitor logs | **No direct edits, no dev installs, no `pip install` outside requirements.txt** |

### Branch strategy

| Branch | Purpose | Who pushes | Who pulls |
|---|---|---|---|
| `main` | Production code | MBP only (after tests pass locally) | Mac Studio pulls |
| `mvp2-production-patches` | Production hardening (this branch) | MBP only | Reviewed, then merged to main |
| `feature/*` | New features / experiments | MBP only | Not deployed until merged to main |
| `MVP2.0`, `MVP2.1` | Historical release branches | (frozen) | Reference only |

### Deploy flow (current — manual)

1. **MBP**: write code, run `bash scripts/test.sh`, commit, push to `main`
2. **GitHub**: receives push, stores it
3. **Mac Studio** (via AnyDesk or SSH):
 ```bash
 cd ~/Desktop/Githubs/video-learning-app
 git fetch origin
 git status # verify clean, on main, no diverged commits
 git pull origin main
 bash scripts/stop.sh
 bash scripts/start.sh
 curl -s http://localhost:8000/api/health # verify alive
 ```

### Deploy flow (future — with GitHub Actions CI/CD)

1. **MBP**: push to `feature/*` branch
2. **GitHub Actions**: runs full test suite
3. **On pass**: GitHub bot comments + auto-merges to `main` (if rules allow)
4. **Mac Studio**: polls GitHub every N minutes (cron job or webhook)
5. **Mac Studio**: pulls + restarts automatically

### Why this architecture (rationale)

1. **Production stability** — Mac Studio serves paying users; no surprise breakages from "let me try this thing"
2. **Dev velocity** — MBP can break things freely without affecting users
3. **Parity** — both machines run Sequoia 15.x (MBP on 15.6.1, Mac Studio on 15.7.9); same major version = same Python/MLX/VideoToolbox ecosystem
4. **Auditability** — every line on Mac Studio came from Git, no ad-hoc edits
5. **Rollback** — `git revert` on main, Mac Studio pulls, instant rollback

### Mac Studio: hard rules

| Rule | Why |
|---|---|
| ❌ Never edit code on Mac Studio | All changes go through git/MBP |
| ❌ Never `pip install` ad-hoc packages | Goes through requirements.txt + git |
| ❌ Never commit directly on Mac Studio | Push from MBP only |
| ❌ Never modify `.env` outside of gitignored path | Config changes go through documented process |
| ✅ Only allowed: `git pull`, restart app, check logs, monitor health |

### Mac Studio: monitored things

```bash
# From MBP (via AnyDesk Terminal):
curl -s http://localhost:8000/api/health         # App alive?
sudo pmset -g | grep sleep                         # Sleep settings intact?
sudo launchctl list | grep video-learning-app      # LaunchDaemon loaded?
df -h /                                             # Disk not full?
tail -50 ~/Desktop/Githubs/video-learning-app/logs/app.log  # Recent errors?
ollama list                                         # Models loaded?
```

### Open questions / future work

| Question | When to address |
|---|---|
| Auto-pull on Mac Studio (cron job or webhook)? | When manual deploy becomes annoying |
| GitHub Actions CI/CD? | When MBP tests start lying (false positives) |
| Blue/green deploy? | When downtime during restart becomes painful |
| Database backup automation? | When first user loses data and complains |
| Monitoring + alerts (Tier 2 above)? | When Mac Studio goes down and you don't notice |
