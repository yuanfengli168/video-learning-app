# Blocker and Challengers

- you will learn a lot from these.

## blockers
- [july 9 2026] transcript panel showing wrong scroll position on page load (MVP2.0 #2)
- [july 6 2026] the incorrect fix for authkit, that login worked, but after 4 fixes not working

---

### ✅ RESOLVED [july 9 2026] — Transcript panel showing 00:10 instead of 00:00 on page load

**Symptom:** On page load, the transcript panel was scrolled to show ~00:10 at the top instead of 00:00. The active highlight was on the correct line (00:00), but it was not visible because the panel was scrolled past it.

**Root cause: `lineEl.offsetTop` was measured from the wrong ancestor.**

The `scrollToTop()` function used `lineEl.offsetTop` to compute the scroll target. `offsetTop` is relative to the element's `offsetParent` — the nearest ancestor with `position != static`. The transcript container (`overflow-y-auto`) has `position: static`, so the transcript lines' `offsetParent` was the page `<body>` (or the main layout div), **not** the container.

For line 0, `offsetTop ≈ 500px` (the height of the header + video player + banner above the transcript). Setting `container.scrollTop = 500 - 4 = 496` scrolled the panel to content offset ~496px, which corresponded to line ~16 (≈ 00:10), not line 0 (00:00).

**Why the unit tests didn't catch it:**
The `FakeLine` mock manually set `offsetTop = 0, 30, 60` — as if the container were the `offsetParent`. In the real browser, the same values would be 500, 530, 560. The tests passed because the mock bypassed the exact thing that was broken.

**Why multiple fix attempts failed:**
1. **"Hover gate"** — I suspected `isHovered=true` was preventing the scroll. Fixed it to force-scroll on init. The panel still scrolled to the wrong line because the underlying position calculation was wrong.
2. **"`pendingForce` stale closure"** — I fixed the rAF's force flag being captured at schedule time. Still showed 00:10 because the scroll target itself was wrong.
3. **"Event handler passes event object as forceScroll"** — Fixed the handler wrapping. Still showed 00:10 for the same reason.
All three were real bugs, but none was the root cause of the scrolling to 00:10.

**Actual fix: use `getBoundingClientRect()` instead of `offsetTop`.**

```js
// Before (broken):
const desired = lineEl.offsetTop - 4;

// After (correct):
const containerRect = container.getBoundingClientRect();
const lineRect = lineEl.getBoundingClientRect();
const lineInContent = container.scrollTop + (lineRect.top - containerRect.top);
const desired = lineInContent - 4;
```

`getBoundingClientRect()` always returns viewport-relative positions. Subtracting the container's top from the line's top gives the line's position relative to the container's visible area. Adding `container.scrollTop` converts that to the absolute position in the scrollable content.

**Files changed:**
- `app/static/js/transcript-follow.js` — `scrollToTop()` rewritten to use `getBoundingClientRect`; plus the 3 secondary fixes (init force-scroll, `pendingForce` variable, event handler wrapping)
- `tests/test_transcript_follow.mjs` — `FakeLine` and `FakeContainer` mocks updated to use `getBoundingClientRect` with the container at viewport y=500; two new regression tests added

**Commit:** `05525ee` (MVP2.0 branch, part C)

---

still not working, but I can choose account on pop up now, but after I choosed 1 account, the link in pop up is this url: "https://video-learning-app-3cf41.firebaseapp.com/__/auth/handler?state=AMbdmDkpfJiJZY7FdIr0qBSD7kACNRacQvgsdOuhttUvvRamuSeRHD1JB9SToANTs6lQXHYfmx3MO2GSoiLthuXNP8pX86HGpPyqe63hzp_edRPROpwQUGjBmdbq71xbS3w4CUEeUfsqijJ4m6VwoWGL0cJdyu-AoOfVyY_C1j2yEuoee0AdtOMiJ2tOZGqr4B05rB-_VitWhleYeqP9YCR1CRkgzreyo34_FRZgKRi5NWRkosc_bJnU4n__XTxk_7zOxH9gcHHmvVMc19NUrqrwdXA1wH9hHqpMg5h4kEVof92TCmyt_YiL-67xjKN7NUYW8pN7AMtHb6LjBEAZn80ypA&iss=https%3A%2F%2Faccounts.google.com&code=4%2F0AdkVLPz5xv1F6bjs6mAu6_2iLuF70CKuFGpAcyBcggsAokbOaGcgJYL9u2bbpk4wXCmriw&scope=email+profile+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile+openid+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email&authuser=0&prompt=none" and it loads loads, there are nothing but white on popup, and few seconds later the popup disappeared, and I am not loggedin.
```

### ✅ RESOLVED — Real root cause and real fix

**Why it looked like the code was fine but login never worked:**
The session cookie from a login *before* the security middleware was added was still valid, so the app appeared to work. Once that cookie expired or was cleared, every fresh login attempt silently failed. Nobody noticed because the failure was invisible in the server logs.

**Two broken security headers in `app/middleware.py`:**

#### 1. `Cross-Origin-Opener-Policy: same-origin` → fixed to `same-origin-allow-popups`

Firebase popup auth works like this:
1. Parent page (`localhost:8000`) opens a popup → `accounts.google.com`
2. User picks their Google account ✅
3. Google redirects popup to `firebaseapp.com/__/auth/handler?code=...`
4. That handler calls `window.opener.postMessage(result, 'http://localhost:8000')` to return the token

With `COOP: same-origin`, the browser **severs `window.opener`** for all cross-origin popups. So step 4's `postMessage` target was `null`. The popup hung blank for ~5 seconds then closed. The parent window never received the token, so the user stayed on `/login`.

**Fix:** `COOP: same-origin-allow-popups` — keeps `window.opener` intact for popups *you* open, while still blocking other origins from navigating into your context.

#### 2. `Cross-Origin-Embedder-Policy: credentialless` on `/login` → fixed to *absent*

Firebase Auth popup mode also creates a hidden iframe at `firebaseapp.com/__/auth/iframe` on the *parent* page to relay auth state. That iframe has no `Cross-Origin-Resource-Policy` header. Under `COEP: credentialless`, Chrome blocked it with `ERR_BLOCKED_BY_RESPONSE (reason: "origin")`. Without the iframe, the popup flow could never complete even after fixing COOP.

**Fix:** Skip `COEP` on the `/login` route only (`app/middleware.py` checks `request.url.path != "/login"`). All other pages still get `credentialless`. The login page is the only page that runs Firebase Auth.

**Files changed:**
- `app/middleware.py` — two header changes above
- `tests/test_security_headers.py` — updated/added tests that document both bugs so they can never silently regress

**Why so many incorrect intermediate fixes:**
The real errors (`window.opener is null`, `ERR_BLOCKED_BY_RESPONSE`) only appear in the *popup window's* DevTools console — not on the parent page. The parent page only sees `auth/popup-closed-by-user`, which looks like a user action. The wrong hypothesis ("authorized domain missing", "needs signInWithPopup directly") sent the investigation in circles.

---

### ✅ RESOLVED [july 9 2026] — Bulk upload returned 404 "Not Found" from the course page

**Symptom:** User selected 3 video files on `/course/<id>` and clicked upload. The page alerted "Bulk upload failed: Not Found". The browser Network tab showed `POST /api/videos/upload-bulk/<real-section-id>` → `404 {"detail":"Not Found"}` with no server-side log of any handler running.

**Root cause: FastAPI route shadowing.**

In `app/routers/videos.py` the routes were declared in this order:
1. `POST /api/videos/upload/{section_id}` (line 45) ✓ matches single upload
2. `POST /api/videos/{video_id}/transcribe` (line 124) ✗ shadows #3
3. `POST /api/videos/upload-bulk/{section_id}` (line 352) ✗ never reached

FastAPI matches routes in declaration order. When the user POSTed to `/api/videos/upload-bulk/6d7...`, FastAPI matched `POST /{video_id}/transcribe` first with `video_id="upload-bulk"`. The transcribe handler then called `db.get(Video, "upload-bulk")`, found nothing, and raised `HTTPException(404, "Video not found")` — the "Not Found" the user saw.

Single upload worked only because `POST /upload/{section_id}` was declared BEFORE `POST /{video_id}/transcribe`. The bulk route was added later (in a new `bulk upload` part A commit) and was added AFTER the transcribe route in the file, so it landed after the shadowing line and was invisible to the router.

**Why the unit tests didn't catch it:**
- All 6 existing bulk tests use `TestClient` and pass with the old route order.
- `TestClient` (Starlette's path lookup) appears to prefer literal-prefix routes over parameterised ones even when declared later, so `POST /api/videos/upload-bulk/<id>` matched the bulk handler in tests but not in production uvicorn.
- The bug was a **production-only** behaviour that TestClient did not reproduce.

**Actual fix:** Move the `POST /upload-bulk/{section_id}` route declaration to be **before** `POST /{video_id}/transcribe`. After the move, both production uvicorn and TestClient route the request to the bulk handler correctly.

Verified with `curl -X POST http://localhost:8000/api/videos/upload-bulk/<real-section-id> -F files=@file.mp4`:
- Before fix: `{"detail":"Not Found"}` (route shadowed)
- After fix: `{"detail":"Not authenticated. Provide a Bearer token."}` (route resolved → auth check) ✓

**Regression guard:** Added two structural tests in `tests/test_videos.py`:
- `test_upload_bulk_route_registered_before_transcribe_route` — asserts the route order
- `test_upload_route_registered_before_transcribe_route` — same for the single-upload route

These tests fail loudly if anyone reorders the routes back to a shadowing position. They don't test the *behaviour* (which TestClient already covers), they test the *structure* (the only thing that actually broke in production).

**Lesson:** When adding a new route to a file that already has a `/{param}/...` route, declare the new route BEFORE the parameterised one. Add a structural test that asserts the order, because behavioural tests in TestClient won't catch the regression.


---

### ✅ RESOLVED [july 9 2026] — 0-byte upload crashes auto-pipeline

**Symptom:** Bulk-uploaded 30 videos, 1 came back as `status=error` with:

```
[Errno 1094995529] Invalid data found when processing input: 
'uploads/d2c902a1-a04b-42eb-a2df-98fb12ce1040.webm'
```

The other 29 went through fine.

**Root cause:** The file was 0 bytes:

```
$ ls -la uploads/d2c902a1-...webm
-rw-r--r--  1 jackyli  staff  0 Jul  9 23:16 ...webm
$ file ...webm
...webm: empty
```

The upload path only validated the **upper** size bound:

```python
file_size = os.path.getsize(file_path)
if file_size > MAX_FILE_SIZE:  # 2 GB cap
    raise HTTPException(413, "too large")
```

It never validated the **lower** bound. So a 0-byte file (browser cancelled, network reset, or wrong file picked) was happily saved to `uploads/` and queued for transcription. Whisper then crashed when it tried to decode empty audio.

**Possible cause of the 0-byte file in this case:** Likely a browser-side hiccup during the 30-file bulk upload — one file's `File` object had no body when the request hit the server. Hard to prove without a browser-side trace.

**Actual fix:** Add a `file_size == 0` check before the size cap. In `upload_video` (single), reject with 400. In `upload_bulk_videos`, mark as `skipped` with a clear error so the rest of the batch continues.

```python
if file_size == 0:
    os.remove(file_path)
    raise HTTPException(
        status_code=400,
        detail="File is empty (0 bytes). The upload may have been cancelled or the source file is broken.",
    )
```

**Tests:** 3 new in `tests/test_videos.py`:
- `test_upload_rejects_zero_byte_file` — single upload returns 400
- `test_upload_zero_byte_does_not_create_db_row` — no orphan file on disk, no DB row
- `test_upload_bulk_skips_zero_byte_file` — bulk: 0-byte skipped, others continue

**Why TestClient didn't catch it originally:** A 0-byte upload only happens via a real browser's `FileList` (cancelled/dropped file). The unit tests always sent non-empty `io.BytesIO(b"x")` data, so the file was never 0 bytes.

**Lesson:** When validating file uploads, check both bounds of the size range, not just the upper one. Empty files are surprisingly common (cancelled uploads, network resets, browser quirks with `FileList` from `<input multiple>`).

### ✅ RESOLVED [july 11 2026] — "Retry 1 failed" button does nothing

**Symptom:** User clicked the "↻ Retry 1 failed" button on a section with 36
videos (section 3 of the "AI 提示词" course). The page reloaded but no animation
or notification appeared, and the one failed video stayed in `status=error`.

**Diagnosis:** The endpoint `POST /api/courses/{c}/sections/{s}/retry-failed`
was filtering by `last_generate_job.status='failed'` and returning
`{retried: 0, video_ids: []}`. But the user's only failed video was a
**transcribe** failure (the 0-byte file uploaded earlier that same session) — it
had `last_transcribe_job.status='failed'` and `last_generate_job=null` because
the pipeline never reached the LLM step. The endpoint saw zero matches and
returned 0, but the UI just reloaded without telling the user why nothing
happened.

**Root cause:** Two coupled problems:
1. The endpoint only knew how to retry *generate* failures, not *transcribe*
   failures. The two-step pipeline (transcribe → generate) can fail in either
   step, but the recovery path only covered one.
2. The UI gave no feedback when the response was `{retried: 0}` — it just
   reloaded silently, leaving the user wondering if the click registered.

**Fix (commit `162c85d`):**
- Endpoint now partitions failures by step:
  - `last_transcribe_job.status='failed'` → re-queue transcribe (auto-pipelines
    to generate via the same `_run_transcribe_job` code path as a fresh
    upload).
  - `last_generate_job.status='failed'` only → re-queue generate (transcript
    is already in the DB, no need to re-transcribe).
  - Videos with BOTH failures go into the transcribe bucket only (the
    transcribe retry will re-do generate as a side effect).
- Response shape extended: `{retried, transcribe_retried, generate_retried,
  video_ids}` so the UI can show "Retrying 1 (1 transcribe, 0 generate)"
  instead of a bare count.
- UI button now shows a spinner + "⏳ Retrying..." label while in flight, and
  on `{retried: 0}` shows a clear toast ("No failed videos found in this
  section") instead of a silent reload.

**Tests:** 2 new in `tests/test_courses.py`:
- `test_retry_failed_section_retries_transcribe_failure` — regression: a
  video with `last_transcribe_job.status='failed'` is now picked up.
- `test_retry_failed_section_response_shape` — covers the mixed case
  (1 transcribe + 1 generate failure in the same section).

**Lesson:** When the user can't tell if a button "did anything", the
endpoint's response is the only feedback signal — make `{retried: 0}` loud,
not silent. And when a pipeline has multiple steps, the recovery endpoint has
to know about *all* of them, not just the last one.


## Challengers:

### 1. ✅ INVESTIGATED [july 11 2026] — 4 GB upload "doesn't proceed" + Whisper local speed ceiling

User reported:
1. `4 GB upload doesn't proceed` (manual todo [jul11] #3)
2. Wants the cap **raised to 10 GB (inclusive)**
3. Asked for a plan to make 100 videos (10 GB total) transcribe in **~1 minute**
4. Confirmed whisper is running **locally on Mac RAM** (`faster-whisper` + CTranslate2)

#### Current state

- `MAX_FILE_SIZE = 2 * 1024 ** 3` in `app/routers/videos.py:36` — 2 GB hard cap
- Default whisper model is `base` (~1.5 GB RAM, ~5x realtime on M-series)
- No server-level request size cap in uvicorn / Starlette, so the only blocker is the application code
- The "4 GB doesn't proceed" is most likely **the 2 GB cap silently rejecting** + (possibly) the browser's "uploading..." spinner stalling on big files because the request takes minutes

#### Speed math (10 GB single video)

A 10 GB `.mp4` at typical 2 Mbps video bitrate is **~80-110 minutes of audio**. The
`base` model does ~5x realtime on an M-series Mac, so a 100-min file = **20 min
of pure transcription** for the transcript step alone — and that's *after* ffmpeg
has decoded the audio, which is another 30-60s for a 10 GB file.

#### Speed math (100 videos, 10 GB total ≈ 100 MB each ≈ 10 min each)

100 × 10-min videos = **~16 hours of audio total**. With current `base` model,
~5x realtime, **serial** = **~3.2 hours of pure transcription** (plus
LLM-material generation, which is its own multiplier).

#### Why 1 minute is mathematically impossible on CPU with the current architecture

For 16 hours of audio to finish in 1 minute we need **~960x realtime**.
- `tiny` model (CTranslate2) on M-series Mac: ~10x realtime → 96 minutes (and ~30% lower WER)
- `base` (current): ~5x realtime → 160 minutes
- `small` (5 GB RAM): ~2x realtime → 480 minutes
- `medium` (10 GB RAM): ~0.5x realtime → 32 hours
- Whisper API on a TPU (Google Cloud): ~10-30x realtime → 30-60 minutes for 16 hours
- 1-hour audio in 1 second on a single A100: physically possible, cost prohibitive

**A single A100 GPU is roughly the floor for 1-minute-for-16-hours-of-audio.** No
amount of code cleverness gets you there on a Mac.

#### Realistic optimization paths (ranked by impact, all local)

| Approach | Speedup | Cost | Risk | Notes |
|---|---|---|---|---|
| A. **Distil-Whisper** (`distil-large-v3`) | **6-7x** vs `large-v3`, same accuracy (~95%) | Switch dep, pull new model | Low | Drop-in `faster-whisper` replacement. Same API. ~750M params vs 1.5B. Best ROI for local. |
| B. **MLX Whisper** (Apple Silicon native) | **3-4x** vs `faster-whisper` CTranslate2 | Rewrite backend (~1 day) | Medium | Uses Apple Neural Engine + Metal. No CUDA dependency. `mlx-whisper` library. **Best for M-series Mac.** |
| C. **whisper.cpp + CoreML** | **2-3x** vs `faster-whisper` | Switch dep, convert models to CoreML | Medium | Lower level than MLX. ANE acceleration. More model format fiddling. |
| D. **Chunk + parallelize** (per video) | **2-4x** if N workers | Need a worker pool (Celery or just `concurrent.futures`) | Medium | Split long audio into 5-10 min chunks, transcribe in parallel, stitch. Per-video only. |
| E. **Switch default to `tiny`** | **2x** vs `base` | -30% WER (accuracy) | Low | Quick win. 1-line config. But "tinny" transcript for a learning app is probably not what you want. |
| F. **Background workers + status polling** | Doesn't speed up 1 video, but lets user **start the next 99** while #1 runs | Needs a queue (Celery / RQ / DB-polling) | Medium | Not a single-video speedup, but a *batch* speedup. Already partially in place via `BackgroundTasks`. |
| G. **Insanely-fast-whisper** (flash-attn + batching) | 5-10x | Needs CUDA GPU | Won't help on Mac | Linux + NVIDIA only. |
| H. **Cloud Whisper API** (OpenAI, AssemblyAI, Deepgram) | ~30x, no local cost | $0.006-0.012/min audio. For 16 hours ≈ **$5.80-$11.50** | Privacy, no offline | Fastest, cheapest at scale. Trade-off: data leaves the box. **MVP3.0 paid tier** (see `doc/MVP3.0-Status.md`). |

#### My recommended path (4 phases)

1. **Phase 1 (this week):** raise `MAX_FILE_SIZE` to `10 * 1024 ** 3`, switch default
   to `distil-whisper` `distil-large-v3` (or `distil-medium.en`). Expected: 100
   videos × 10 min = ~30-40 min total on a current M-series Mac. *Goal: 1 hour of
   audio in ~2 min, 16 hours in ~30 min.*
2. **Phase 2 (next week):** rewrite `app/services/transcription.py` to use
   `mlx-whisper` instead of `faster-whisper` on M-series. Same Whisper weights,
   different runtime. Keep `faster-whisper` as fallback for non-Apple-Silicon.
   Expected: 3-4x further speedup.
3. **Phase 3 (MVP3.0):** background worker pool that processes N videos in
   parallel, with a status poller on the UI. Single 10 GB video still takes
   20-30 min, but the user can queue 100 and walk away.
4. **Phase 4 (MVP3.0 paid tier):** opt-in "Cloud Whisper" button per video, billed
   per minute. **1 hour of audio in ~1-2 min** for $0.36-0.72. Tracked in
   `doc/MVP3.0-Status.md`.

#### On the 1-minute target

To make 16 hours of audio finish in 1 minute, you need a single A100 (or equivalent)
GPU running Whisper-large-v3 with batching and flash-attention. That's a **~10-20
year payback** at AWS rates for a personal-use app.

**Realistic floor for local-only, no GPU:** ~30-40 min for 16 hours of audio, with
distil-whisper + mlx + chunked parallel processing. That's the best you'll get
without renting a GPU.

**If 1 min is a hard requirement:** cloud Whisper API. ~$12 for 16 hours of audio,
finishes in ~30 min serial or ~5 min with 10 parallel requests. Documented in
`doc/MVP3.0-Status.md` as the MVP3.0 paid tier.

#### What's NOT going to be done

- Custom Whisper model fine-tune (cost: 100+ GPU-hours, no clear win)
- ONNX runtime experiment (similar to mlx-whisper, less M1-friendly)
- Speculative decoding (faster-whisper doesn't support it; whisper.cpp partial)

#### Files affected (when implemented)

- `app/routers/videos.py` — change `MAX_FILE_SIZE`
- `app/services/transcription.py` — backend swap
- `app/config.py` — add `WHISPER_BACKEND` (faster-whisper | mlx-whisper | cloud)
- `app/models/video.py` — add `whisper_backend` column
- `app/templates/video.html` — show "transcribing..." with progress + backend label
- `scripts/setup.sh` — pip-install `mlx-whisper` on M-series only

#### Status

Investigated, recommendation in `doc/MVP3.0-Status.md` row 1 (raise cap) and row
2 (whisper backend). 1-minute target is a **paid tier / cloud** feature, not a
local-only one. Awaiting user direction on whether to start Phase 1 this week.
### 2. Whisper model compare:

1. Distil-Whisper (Option C)
What it is: A distilled version of OpenAI's whisper-large-v3. Same architecture, but a team at Hugging Face trained it using a "pseudo-labeling" technique where the original large-v3 model produced labels, then a smaller student model was trained to mimic those labels. The result is roughly half the parameters of large-v3 with ~95% of the accuracy.

Two variants you'll see:

distil-large-v3 (756M params, vs large-v3's 1.5B) — recommended
distil-medium.en (394M params, English-only) — even faster, lower accuracy
Why it's faster: The standard tiny/base/small/medium Whisper models are already "distilled" in a sense (smaller variants of large). Distil-large sits in the middle — bigger than small (244M), smaller than medium (769M), but with large-v3's training. So you get large's quality at small's speed.

Runtime: Same faster-whisper (CTranslate2) engine you already use. Drop-in replacement, just change the model name. No new dependency, no Mac-specific code path.

Mac chip minimum: Anything that runs faster-whisper today. M1+ recommended, Intel Macs work but are slow. The 6-hour job on M1 Pro with distil-large-v3 would take ~25-40 min.

2. MLX Whisper (Option D)
What it is: Apple's MLX framework is a NumPy-like array library designed specifically for Apple Silicon. It uses the Neural Engine (ANE) + Metal GPU for compute, not just CPU. mlx-whisper is a port of Whisper to MLX by the community.

Why it's faster than faster-whisper on Mac: faster-whisper uses CTranslate2 (CPU-optimized, very efficient on x86). On Apple Silicon, CTranslate2 only uses the performance cores (P-cores) and doesn't touch the ANE — which is the dedicated ML hardware on M-series chips. MLX uses the ANE directly, so it gets 3-4x more compute throughput on the same hardware.

Mac chip minimum: M1 or newer ONLY. Intel Macs cannot use MLX — no ANE, no Metal compute path. If you have an M1/M2/M3/M4 it'll work; on M1 Pro/Max/Ultra it'll be even faster because they have more ANE cores and more GPU cores.

Mac chip optimal: M1 Pro / M2 Pro / M3 Pro / M4 Pro (16-core ANE, 16-19-core GPU). The M1/M2/M3/M4 Max chips are even better (32-core ANE).

Compatibility check:

3. whisper.cpp + CoreML (Option E)
What it is: Two pieces glued together:

whisper.cpp: Georgi Gerganov's C++ port of Whisper. Runs on basically anything (CPU, GPU via Metal/CUDA/OpenCL/ANE).
CoreML: Apple's framework for running ML models on ANE/GPU. You convert the Whisper model to CoreML format (a one-time step per model size).
Why faster than faster-whisper on Mac: Like MLX, it uses the ANE. But unlike MLX, whisper.cpp has been around longer (since 2022), is much more mature, and supports more platforms (Mac, Linux, Windows, even Raspberry Pi).

Mac chip minimum: M1+ (because CoreML is Apple-only). But the conversion step is finicky — you need coremltools and the conversion can fail on edge cases.

Why we ranked it below MLX: More setup, more moving parts, less Pythonic. MLX is a "pip install and go" experience for Whisper; whisper.cpp+CoreML requires you to convert each model variant you want to use.

4. Insanely Fast Whisper (Option G)
What it is: A Python wrapper around faster-whisper that adds batched inference + flash attention via PyTorch. Designed to max out a beefy NVIDIA GPU.

Why it doesn't help you on Mac:

Flash attention is a CUDA-specific optimization (uses Tensor Cores)
ANE can't run flash attention
The batching technique works on any GPU but needs > 8 GB VRAM
Apple's Metal GPU doesn't have an equivalent of Tensor Cores
Mac chip minimum: None — it simply won't work on Mac. You'd need a Linux box with an A100/H100. The "minimum" to even get a 2x speedup is an RTX 3090 (24 GB VRAM); the "you'll actually see 10x" is an A100 (80 GB).

Speed comparison for your scenario: 6 hours of audio, 10 GB video
Assuming you're on a 2021 MacBook Pro M1 Pro / 16 GB (a realistic baseline for the user):

Backend	Model	Est. time for 6h audio	Realtime factor	Notes
faster-whisper (current)	base	~72 min	5x	What you have today
faster-whisper (current)	small	~180 min	2x	Worse — not recommended
faster-whisper + distil	distil-large-v3	~25-30 min	12-15x	Drop-in change, 1 line of config
MLX Whisper	large-v3	~15-20 min	20-25x	Mac M-series only, new dep
MLX + distil	distil-large-v3	~8-12 min	30-45x	Best of both worlds
whisper.cpp + CoreML	large-v3	~18-25 min	15-20x	More setup, finicky
Insanely Fast Whisper	large-v3	N/A on Mac	—	Linux + NVIDIA only
Cloud Whisper API	large-v3	~1-2 min	200-400x	$0.36-0.72 per 6h file
Sanity check on the math: 6 hours of audio at 5x realtime = 6 × 60 / 5 = 72 min ✅. At 30x = 12 min ✅.

Which one should you actually pick?
If you want easiest, biggest win this week (1 day of work):

Distil-large-v3 via faster-whisper. Zero new dependencies, just change model_size from "base" to "distil-large-v3". 6h audio: ~25-30 min (down from 72 min, ~3x faster with large-v3 accuracy). Whichever Mac you have works.
If you want best local performance, period (1-2 days of work):

MLX Whisper with distil-large-v3. New dep, requires Apple Silicon, but 6h audio: ~8-12 min (~7x faster than current, near real-time). Your transcribe step basically disappears as a wait.
If you want truly fast, don't care about cost (paid tier, MVP3.0):

Cloud Whisper API. 6h audio: ~1-2 min. The only path to your "1 minute" goal. ~$2-4 per 10 GB video.


### 2.2 Trial on Faster Whisper models (local best and fast, loacl best and extremely fast)
- so we add these 2 picks in selection on top of tiny, base, small, medium: local best and fast, loacl best and extremely fast
  - local best and fast: Distil-large-v3 via faster-whisper
  - loacl best and extremely fast MLX Whisper with distil-large-v3
Can we have 5 thing together? (you mentioned MLX Whisper with distil-large-v3 need new dep? can it used with all other 4? what do you mean by dep?)
#### Resolution [2026-07-11]

User picked options A, A, A:
1. **Default = "Local best and extremely fast"** (MLX + distil-large-v3)
   when MLX is available, else fall back to "Local best and fast"
   (Distil via faster-whisper), else the legacy `base`.
2. **MLX auto-fallback on non-Apple-Silicon**: silently fall back to
   the faster-whisper smart pick and surface a warning via
   `whisper_fallback_reason` on the video row. The UI can show the
   "actually ran X" message in a follow-up.
3. **UI = grouped optgroup** — 4 originals under "Manual (pick a size)",
   2 new under "Smart picks (recommended)".

User's question "what do you mean by dep?" — short answer: a `pip install`
Python package. `mlx-whisper` is the new dep. The 5 things (4 manual + 1 fast
smart pick that ALSO uses faster-whisper) work together fine without any
new dep. Only the 6th option ("extremely fast" via MLX) needs the
`mlx-whisper` install. The dropdown shows all 6; if the user picks
"extremely fast" on an Intel Mac, we auto-fall back to "fast".

User's M1 Max confirmed: `sw_vers` → `arm64 Apple M1 Max`. MLX will
work natively (16-core ANE, 32-core GPU, 400 GB/s memory bandwidth).

**Status:** Plumbing shipped in `2a96049` + `1497cd7`. Actual MLX
implementation deferred until user runs `pip install mlx-whisper`
on the M1 Max. See `doc/MVP3.0-Status.md` row 2 (status:
"Plumbing done; MLX backend itself still raises `NotImplementedError`").
