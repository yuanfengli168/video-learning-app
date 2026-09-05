# MLX Transcription — Incident Postmortem & Production Runbook

**Date:** 2026-09-04 → 2026-09-05
**Branch:** `mvp2-production-patches`
**Status:** ✅ Resolved — transcription verified working end-to-end

This document covers four stacked issues that broke `local-large-turbo`
(MLX Whisper) transcription over two days, the final architecture
that fixed them, and the operational caveats every future maintainer
needs to know. Written during the pre-9/9-go-live hardening window.

---

## 1. What happened (timeline)

| Date | Event |
|---|---|
| 09-03 | 1.6 GB upload stuck at "transcribing" forever. Root cause #1: `mlx-whisper` package missing from venv (silent pip failure at venv creation). Installed it. Root cause #2 surfaced: `huggingface.co` DNS-poisoned → model download hangs forever (silent, no error in job JSON). |
| 09-04 | Stale VPN found and stopped → HF reachable → pre-cached ALL 5 whisper models (MLX turbo 1.6 GB + tiny/base/small/medium CPU models). First real MLX transcribe attempt: gunicorn worker **SIGABRT crash** ("Python quit unexpectedly"). |
| 09-05 | Crash reproduced + isolated: MLX works fine in fresh processes on Python 3.14, but crashes in forked gunicorn workers. **Subprocess fix applied.** One transient `metal::Device XPC_ERROR_CONNECTION_INVALID` during reboot window. After server restart, full transcribe succeeded (27k-char transcript, ~2 min for a 30-min video). |

---

## 2. The four root causes

### 2.1 Silent pip failure → missing `mlx-whisper`
The venv (created Aug 23) was missing `mlx-whisper` despite it being in
`requirements.txt` (added Aug 19). The install line fails silently on
Python 3.14 because `mlx` had no 3.14 wheel at the time. The app's
`is_mlx_available()` gate then correctly fell back to CPU `base` —
which is why transcription "worked but slowly" a week ago.

**Caveat:** `pip install -q -r requirements.txt | tail -1` in
`scripts/setup.sh` **hides install errors**. If a whisper model resolves
to CPU unexpectedly, run `venv/bin/pip install -r requirements.txt`
without `-q` and check for wheel failures.

### 2.2 `huggingface.co` DNS poisoning (stale VPN)
A VPN client was running but not routing properly. System DNS
(`114.114.114.114`, `223.5.5.5` — mainland resolvers) answered
`huggingface.co` with **fake IPs** (Twitter's/Cloudflare-adjacent
ranges), so connections were actively reset
(`Errno 54 Connection reset by peer`). `hf-mirror.com` is NOT a
workaround — it 308-redirects to huggingface.co.

**Caveat:** if model downloads hang with no job error, check:
`dig +short huggingface.co` — anything NOT in `13.35.x` / CloudFront
ranges means DNS is poisoned. Fix the VPN, don't debug the code.

**Mitigation (now permanent):** all 5 models are pre-cached in
`~/.cache/huggingface/hub/`. Transcription never contacts HF anymore.
Only *new* model downloads need a clean network.

### 2.3 MLX + gunicorn fork = SIGABRT crash ⚠️ THE BIG ONE
**Symptom:** every `local-large-turbo` transcribe killed a gunicorn
worker with SIGABRT. macOS crash report:
`"crashed on child side of fork pre-exec"`, faulting thread frames:
`libmlx.dylib: mlx::core::eval_impl ← _PyManagedBuffer_FromObject ←
NumPy _array_from_array_like`.

**Root cause:** NOT a Python 3.14 incompatibility (proven: MLX works
flawlessly in fresh processes on 3.14 + mlx 0.32.2). The problem is
fork-safety: gunicorn workers are forked children (`preload_app=True`),
and the BackgroundTask thread forks ffmpeg (language detection) while
Metal/MLX thread-pool state is live → macOS's fork-safety aborts the
process. Intermittent variant: `metal::Device: Unable to build metal
library from source / XPC_ERROR_CONNECTION_INVALID` — corrupted
inherited Metal state instead of a hard abort. **A laptop restart does
NOT fix either variant.**

**The fix (architecture change):**
1. **`scripts/mlx_transcribe_worker.py`** — standalone MLX transcriber.
   `transcribe_with_backend()` (mlx branch) now spawns it as a
   **subprocess** and parses its JSON stdout. Metal/GPU state lives and
   dies entirely inside that fresh child process — a crash there can
   never take down gunicorn, and the fork-safety abort can't happen
   because the worker is a fresh exec, not a fork-with-Metal-state.
2. **`detect_audio_language()` always uses CPU `tiny`** now (was: MLX
   turbo). Detection only needs the language token from a few 30s
   windows; `tiny` is 75 MB, cached, fast, and Metal-free — the
   ffmpeg-fork-per-window detection loop was the primary crash trigger.

**Tests:** `tests/test_whisper_picker.py` — the 3 mlx-path tests now
assert the subprocess contract (mock `subprocess.run`, verify CLI argv
+ JSON parsing). 43/43 passing.

### 2.4 Old code still running after edits (preload_app gotcha)
After the fix was written to disk, transcription still failed — because
the gunicorn master (started *before* the fix, `preload_app=True`) kept
forking workers from the old preloaded code. **Editing a file does
nothing until the server restarts.**

**Caveat:** after ANY code change: `bash scripts/restart.sh`, then
verify the master's start time: `ps -o pid,lstart -p $(pgrep -f
"gunicorn -c gunicorn.conf" | head -1)`.

---

## 3. Operational caveats (the runbook part)

### 3.1 After a reboot — bring everything up manually
There is **no LaunchDaemon for gunicorn** (yet — `scripts/
install-launchdaemon.sh` exists but was never run). After every reboot:

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app
bash scripts/restart.sh            # gunicorn + app
# Ollama: brew services start ollama  (or open the app)
curl -s http://localhost:8000/api/health   # verify {"status":"ok"}
```

**Ollama does not auto-start either.** If generate fails with
`provider_unavailable: All providers in your tier's chain failed`,
Ollama isn't running (this exact failure was observed post-reboot on
09-05: transcription succeeded, generation failed).

### 3.2 Metal XPC transient errors during boot
`XPC_ERROR_CONNECTION_INVALID (is the OS shutting down?)` can appear if
a transcribe fires during/right after a reboot or login transition.
The Metal shader compiler XPC service needs a settled user session.
**Self-recovers — just re-click Transcribe once the desktop is stable.**

### 3.3 Transcription architecture (what to expect in `ps`)
A healthy MLX transcribe shows a separate process:
```
python scripts/mlx_transcribe_worker.py <file> --model mlx-community/whisper-large-v3-turbo [--language <iso>]
```
Expected timing: language detect (CPU tiny) ~10–20s → MLX turbo
~5–10× realtime (30-min video ≈ 2 min, 2.5h video ≈ 20–30 min).

### 3.4 Diagnosing a stuck "transcribing" row
If a row sits in `transcribing` with progress 0 forever (network hang
class of failure), `scripts/fix_stuck_transcribe.py` marks it `error`
with a clear message after a threshold (default 15 min):
```bash
venv/bin/python scripts/fix_stuck_transcribe.py           # dry-run
venv/bin/python scripts/fix_stuck_transcribe.py --apply    # mark failed
```

### 3.5 Offline model installs
`scripts/install_local_whisper_model.py` wires manually-downloaded
model files into the HF cache (for machines where HF is unreachable).
Usage + file list in its docstring. Not currently needed (all models
pre-cached).

### 3.6 Model pre-cache inventory (2026-09-04)
| Model | Size | Backend | Status |
|---|---|---|---|
| `mlx-community/whisper-large-v3-turbo` | 1.61 GB | mlx (subprocess) | cached |
| `tiny` | 75 MB | faster-whisper CPU | cached |
| `base` | 142 MB | faster-whisper CPU | cached |
| `small` | 484 MB | faster-whisper CPU | cached |
| `medium` | 1.5 GB | faster-whisper CPU | cached |

`tiny` doubles as the language-detection model (see 2.3).

---

## 4. Files changed in this fix

| File | Change |
|---|---|
| `scripts/mlx_transcribe_worker.py` | **NEW** — subprocess MLX transcriber (CLI in, JSON out) |
| `app/services/transcription.py` | mlx branch → subprocess dispatch; `detect_audio_language` → CPU `tiny` always |
| `tests/test_whisper_picker.py` | 3 mlx tests updated to subprocess contract |
| `scripts/fix_stuck_transcribe.py` | **NEW** — un-stick 'transcribing' rows (dry-run default) |
| `scripts/install_local_whisper_model.py` | **NEW** — offline model cache installer |

## 5. Known-issues / follow-ups for 9/9 go-live

1. **Install the LaunchDaemon** (`scripts/install-launchdaemon.sh`) so
   reboots can't leave the app down. Also fixes AnyDesk-into-login-
   screen blind spots (server should never depend on a GUI session).
2. **`setup.sh` should not swallow pip errors** (`-q | tail -1`).
3. The generate chain relies on Ollama auto-start; consider a brew
   service or launchd agent for Ollama too.
4. `_run_transcribe_job`'s except-path nested `db.get` can leave
   `video.status` as `transcribing` while the job JSON says `failed`
   (observed once on 09-03). The status column and job JSON disagree
   until reconciled. Low priority — the UI shows the job error either
   way, and `reconcile_video_status` covers the common path.