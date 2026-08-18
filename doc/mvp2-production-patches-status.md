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
