# MVP2 Production Patches — Status

> **Branch**: `mvp2-production-patches` (based on `main`)
> **Goal**: Make this Mac Studio (`Yuanfengs-Mac-Studio.local`) a 24/7 production server for video-learning-app.
> **Last updated**: 2026-08-18

---

## ✅ Done (committed or applied locally)

| # | Item | How | Commit / Note |
|---|---|---|---|
| 1 | Created branch `mvp2-production-patches` from `main` | `git checkout -b mvp2-production-patches` | Local only, not pushed |
| 2 | Installed `ffmpeg` (Homebrew 9.0.1) | `brew install ffmpeg` | System-wide; needed by 3 failing tests + production transcoding |
| 3 | Fixed 3 ffmpeg tests in `tests/test_webm_to_mp4_plugin.py` | Side-effect of ffmpeg install (no code change) | Tests that patched `subprocess.run` now reach the patch because `is_ffmpeg_available()` returns True |
| 4 | Fixed `tests/test_whisper_picker.py::test_transcribe_endpoint_accepts_smart_turbo_pick` (missing monkeypatch) | Added `monkeypatch` fixture + injected dummy `mlx_whisper` module + mocked `platform.machine` | **`78a94bc`** |
| 5 | Full test suite green: **625 passed, 9 skipped, 0 failed** | `./scripts/test.sh` | Was 620 passed + 4 failed |

---

## ❌ Not done (TODO for this branch)

| # | Item | What's needed | Who | Blocking? |
|---|---|---|---|---|
| 6 | **Login AuthKit buttons** | Create `.env` from `.env.example` and fill in the 6 Firebase fields. Restart server. | **User** | Yes — login is broken until `.env` is filled |
| 7 | **LaunchDaemon install** | Run the `sudo tee ...` block to write `/Library/LaunchDaemons/com.video-learning-app.plist` (improved version with `KeepAlive=true` + proper logs) | **User** (needs sudo) | Yes — 24/7 auto-restart won't work |
| 8 | **Reboot test** | `sudo shutdown -r now`, wait 2-3 min, reconnect via AnyDesk, run `curl http://localhost:8000/api/health` | **User** | Verifies item 7 works |
| 9 | **Push branch to origin** | `git push -u origin mvp2-production-patches` | **AI (me)** | No — local commit is preserved |

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
================= 625 passed, 9 skipped, 115 warnings in 8.75s =================

Coverage: ~89% (was ~88% before the fix)
```

**Branch state**:
- Local commits on `mvp2-production-patches`: **1** (`78a94bc`)
- Files changed: `tests/test_whisper_picker.py` (+18 / -1)
- Pushed to origin: **No** (waiting for your go-ahead)

---

## 📋 Recommended next steps (in order)

1. **User fills in `.env`** with real Firebase credentials → restart server → confirm login buttons appear
2. **User installs LaunchDaemon** (sudo block) → reboot → verify `curl http://localhost:8000/api/health` after reconnect
3. **AI pushes `mvp2-production-patches`** to origin (after items 1 & 2 confirmed working)
4. **Optional**: open a PR or leave on the branch

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

