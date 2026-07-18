# MVP2.1-all — 2026-07-16 (updated 2026-07-18: 2.1.0 shipped)

> **TL;DR**: MVP2.0 is closed on `main` (583/583 tests,
> 92% coverage, tag v2.0.8). MVP2.1 is a **focused
> 2-item release** on a **new branch `MVP2.1`**.
> **MVP2.1.0 (Plugin Tools + WebM→MP4) is SHIPPED** in
> this commit-cycle. MVP2.1.1 (Worker pool) is the next
> item.
>
> 1. ✅ **Item 4 — Plugin Tools tab + WebM→MP4**
>    *(shipped as 2.1.0)*: an extensible tab where users
>    can pick from installable tools. v1 ships with
>    WebM→MP4 conversion via ffmpeg. **Side-by-side
>    transcode** (not in-place) — the new file is
>    written next to the original, the original is never
>    modified. See `doc/MVP2.1-Status.md` for the full
>    per-design-decision rationale.
>
> 2. 🟡 **Worker pool, throttle=3, configurable**
>    *(next, 2.1.1)*: a background worker pool that lets the user
>    queue 100 videos and walk away, instead of
>    `BackgroundTasks` running them serially in the
>    request-handling process. `throttle=3` is the default
>    concurrency; configurable via env var so power users
>    can crank it to 6-8 on an M-series Mac.
>
> **Branch:** `MVP2.1` (new branch off `main` after MVP2.0
> merges, or off `MVP2.0` if MVP2.0 hasn't merged yet).
> **Versions:** `2.1.0` (Plugin Tools) → `2.1.1` (Worker
> pool).

---

## 1. Phasing recap (where we are)

| Version | Status | What it shipped |
|---|---|---|
| MVP1.0 | ✅ Shipped | Transcribe + LLM materials (mindmap, summary, quiz, chat) |
| MVP2.0 | ✅ Shipped (`MVP2.0` branch, 67 commits, 552 tests) | Bulk upload 10 GB, language policy, Discuss citations, tab switching, per-step timing, logout fix, distil cleanup, section-videos panel |
| **MVP2.1** | 🟡 **THIS DOC** | Plugin Tools + Worker pool (2 items) |
| MVP3.0 | 📋 Planned (`doc/MVP3.0-Status.md`) | OCR, cloud Whisper, soft-delete, etc. (deferred) |

---

## 2. Item 4 — Plugin Tools tab + WebM→MP4 (MVP2.1.0)

### What

A new **"Tools" tab** in the video page (next to
Summary, Transcript, Mindmap, Quiz, Discuss, Chat). The
tab shows a list of available plugins; each plugin is a
named, clickable action that operates on the current
video.

**v1 ships with one plugin: WebM → MP4 conversion.** The
plugin uses `ffmpeg` (system binary) to transcode the
uploaded file, then either:

- **Option A (recommended)**: in-place transcode
  (replaces the original file in `uploads/`, updates the
  `Video` model's `path` and `transcribed_at` to NULL
  because the new file is "fresh").
- **Option B**: side-by-side transcode (writes a new
  file, the user can choose to delete the original).
  Adds a delete button to the Tools tab in MVP2.2+.

User explicitly chose **Option A** for v1 (least UI
clutter); Option B deferred.

### Why now

manualTodo [july14] #4: "can we add a new function called
transfer webm to MP4, but not as part of the main
functions". User tagged it for **MVP2.1-plugins** in
the MVP2.0 closure discussion.

### Scope

**In scope for 2.1.0:**
- New "Tools" tab in the video page UI
- Plugin registry pattern (Python dict, similar to
  `MODEL_REGISTRY` in `app/services/transcription.py:31`)
- WebM→MP4 plugin (ffmpeg-based)
- Plugin result display: success message with the new
  file size, or error message with ffmpeg's stderr
- Plugin permission model: any logged-in user can run a
  plugin on their own videos (no cross-user access)
- Audit log entry for every plugin run (which user, which
  video, which plugin, success/failure, duration)

**Out of scope (deferred to MVP2.2+ or MVP3.0):**
- Other plugins (extract-audio, fix-metadata,
  thumbnail-generator, etc.) — easy to add via the
  registry, but not v1
- Plugin install/upgrade flow (this is a self-hosted app,
  plugins ship with the app)
- Plugin permissions / quota (any logged-in user can
  run any plugin on their own videos; abuse unlikely on
  a single-user dev box)

### Design decisions (proposed, to confirm with user before implementation)

- **Plugin = Python function** in
  `app/services/plugins.py`, registered in a dict like:
  ```python
  PLUGIN_REGISTRY: dict[str, PluginSpec] = {
      "webm_to_mp4": PluginSpec(
          label="Convert to MP4 (smaller file, broader compatibility)",
          input_types={"video/webm"},
          function=transcode_webm_to_mp4,
          requires=["ffmpeg"],
      ),
  }
  ```
  Plugins are NOT loaded from disk dynamically (no
  security audit needed, no install flow). Adding a new
  plugin = adding one entry to the dict.

- **ffmpeg detection at startup** — if ffmpeg is not on
  `$PATH`, the Tools tab shows "ffmpeg not found" with
  install instructions (`brew install ffmpeg`). Don't
  crash the app at startup.

- **Run in BackgroundTasks** for v1 (same process, NOT
  the worker pool). Reason: Worker pool ships in 2.1.1
  (next), and we don't want MVP2.1.0 to depend on
  un-shipped infrastructure. Reuse the existing
  `_run_transcribe_job` pattern.

- **After transcode, re-enqueue transcription** with
  `transcribed_at` reset to NULL and status set to
  `pending`. The plugin's audit log entry makes the
  reset obvious ("transcode → re-transcribe by user X
  at 14:32").

### Tests

- `tests/test_plugin_registry.py` — registry has the
  expected v1 plugins, ffmpeg-missing case is handled
  gracefully
- `tests/test_webm_to_mp4_plugin.py` — happy path
  (small WebM → MP4 via ffmpeg), ffmpeg-missing case,
  error case (corrupt WebM, ffmpeg returns non-zero)
- `tests/test_tools_tab_rendering.py` — Tools tab
  appears in the tab bar, shows the v1 plugin, shows the
  "ffmpeg not found" message when ffmpeg is absent
- `tests/test_plugin_audit_log.py` — every plugin run
  writes one row to the audit log table

**Estimated test count:** 12-16 new tests, 564-568 total.

### Effort / risk

| Dimension | Estimate |
|---|---|
| Effort | 2-3 days |
| Risk | Low (isolated new feature, no changes to existing routes/models) |
| Dependencies | ffmpeg on user's Mac (already a soft dep; needed for some upload validations) |

---

## 3. Worker pool, throttle=3, configurable (MVP2.1.1)

### What

Replace FastAPI's in-process `BackgroundTasks` with a
**real background worker pool** that processes jobs from
a queue. The current `BackgroundTasks` runs jobs serially
in the request-handling process; if a user uploads 100
videos, they all start in the same event loop and the
last one waits hours.

**v1 ships with:**
- A Python `concurrent.futures.ThreadPoolExecutor` with
  `max_workers=THROTTLE` (default 3, configurable via env
  var)
- A simple in-memory queue (Python `queue.Queue`)
- Status polling: the existing `/api/jobs/{id}` endpoint
  now returns the queue position, worker assignment, and
  progress

**v1 does NOT ship with:**
- Redis / RabbitMQ / external broker (overkill for
  single-user dev box)
- Cross-process workers (no need yet; the user runs 1
  uvicorn process)
- Persistent job queue (jobs lost on restart; OK for v1
  since the user is the only one running the app)

### Why now

MVP3.0 #4 (P1) is "Background worker pool + status
polling for batch uploads". User moved it into MVP2.1
because the Plugin Tools tab (2.1.0) also needs to run
async, and it's cleaner to ship the worker pool as
infrastructure that both features depend on, than to
have Plugin Tools hack around `BackgroundTasks`.

### Scope

**In scope for 2.1.1:**
- `app/jobs.py` (or new `app/services/worker_pool.py`):
  `WorkerPool` class wrapping
  `concurrent.futures.ThreadPoolExecutor`
- Env var `WORKER_POOL_SIZE` (default `3`)
- Graceful shutdown: drain in-flight jobs on SIGTERM
- Status API enhancement: `/api/jobs/{id}` returns
  `{status, position_in_queue, worker_id, started_at,
  progress_pct}`
- Migration: replace `BackgroundTasks` in
  `app/routers/videos.py` and `app/routers/generation.py`
  with `WorkerPool.submit()`
- Plugin Tools (2.1.0) refactored to use the new pool
  instead of `BackgroundTasks`

**Out of scope (deferred to MVP3.0):**
- Cross-process workers (multiprocessing or separate
  worker process started by `start.sh`)
- Persistent queue (SQLite-backed or Redis)
- Job retry / dead-letter queue
- Per-user quotas
- Real-time progress streaming (SSE or WebSocket; today
  the client polls every 2s)

### Design decisions (proposed, to confirm with user before implementation)

- **`max_workers=3` default** — calibrated for M-series
  Mac (8-10 cores, so 3 transcription threads + the main
  uvicorn process + the user's browser leaves headroom).
  On a 4-core Intel i5, `3` is still safe (leaves 1 core
  for uvicorn). User can set `WORKER_POOL_SIZE=1` for
  very-low-end machines.

- **ThreadPoolExecutor, not ProcessPoolExecutor** —
  transcription is I/O-bound (mostly waiting on
  faster-whisper / mlx-whisper), not CPU-bound. Threads
  share memory, so the model cache in
  `app/services/transcription.py:26` is shared across
  workers (each worker reuses the cached model, no
  per-worker load).

- **FIFO queue, no priority** — uploads go in order they
  arrive. A user uploading 100 videos gets all 100
  processed in order; a user uploading 1 video later
  still has to wait for the 100-video batch to finish
  (or cancel them). Acceptable for v1.

- **Job deduplication** — if the user clicks "Upload"
  twice on the same file (browser double-click), only 1
  job runs. v1 dedup by `(filename, size, mtime)`. Good
  enough for the dev-box use case.

### Tests

- `tests/test_worker_pool.py` — unit tests for
  `WorkerPool`: submit, drain, status, graceful shutdown
- `tests/test_worker_pool_concurrency.py` — integration
  test: submit 10 jobs, assert exactly 3 run in parallel
  (use a slow-mock job that sleeps for 5s, measure that
  wall-clock is ~15s for 10 jobs, not 50s)
- `tests/test_worker_pool_status_api.py` —
  `/api/jobs/{id}` returns the new fields
- `tests/test_jobs_deduplication.py` — double-click
  upload dedups correctly
- `tests/test_existing_jobs_still_work.py` — regression:
  the existing transcribe/generate flows still complete
  end-to-end after the `BackgroundTasks` →
  `WorkerPool` swap

**Estimated test count:** 15-20 new tests, ~580-588
total.

### Effort / risk

| Dimension | Estimate |
|---|---|
| Effort | 1-2 weeks (option A: 1-2 days if we just swap `BackgroundTasks` for `ThreadPoolExecutor.submit()` and don't add the status API) |
| Risk | Medium (touches the core job-dispatch path; regression risk on the existing transcribe → generate flow) |
| Dependencies | Should ship AFTER 2.1.0 (Plugin Tools), so the plugin can use the pool |

---

## 4. Order of work

1. ✅ **2.1.0 (Plugin Tools)** — **shipped** (2-3 days,
   took 1 day including tests + docs)
2. 🟡 **2.1.1 (Worker pool)** — 1-2 weeks (option A: 1-2
   days for the no-status-API version), up next

Reasoning: Plugin Tools was more isolated (new feature,
no changes to existing routes), so it shipped first to
de-risk the branch. Worker pool touches the core
job-dispatch path, so it goes second and can reuse the
Plugin Tools' ffmpeg-detection pattern.

---

## 5. Success criteria

MVP2.1 is "done" when:

- [ ] `tests/` count ≥ 580, all passing, 87%+ coverage
  maintained
- [ ] `app/services/plugins.py` exists with the v1
  plugin
- [ ] Tools tab appears on the video page, shows the v1
  plugin, handles the ffmpeg-missing case
- [ ] Worker pool replaces `BackgroundTasks` in
  `videos.py` and `generation.py`; existing tests still
  pass
- [ ] `WORKER_POOL_SIZE` env var is documented in
  `scripts/setup.sh` and `Readme.md`
- [ ] CHANGELOG has `2.1.0` and `2.1.1` entries
- [ ] `doc/MVP2.1-all.md` (this file) is updated to
  reflect what actually shipped (replace "Proposed"
  with "Shipped")
- [ ] Branch `MVP2.1` is merged to `main` and a
  `v2.1.1` tag is pushed

---

## 6. What's NOT in MVP2.1 (carried forward to MVP3.0)

These items are explicitly deferred from MVP2.0 / 2.1
into MVP3.0. They are listed in `doc/MVP3.0-Status.md`
and reproduced here for completeness:

- Item 1: MAX_FILE_SIZE 2 GB → 10 GB ✅ already shipped in
  MVP2.0
- Item 2: Whisper model picker ✅ already shipped in
  MVP2.0
- Item 3: Cloud Whisper API (paid tier) — MVP3.0 P2
- Item 4: **Background worker pool** — pulled forward to
  MVP2.1.1 ✅
- Item 5: Soft-delete / trash / restore — MVP3.0 P1
- Item 6: Note section — MVP3.0 P1
- Item 7: Video player scroll-to-end — MVP3.0 P1
- Item 8: Per-step timing badge — ✅ already shipped in
  MVP2.0.4
- Item 9: Language consistency in materials — MVP3.0 P2
- Item 10: OCR of video frames — MVP3.0 P3
- Item 11: Data flow chart — MVP3.0 P2
- Item 12: Jira via MCP — MVP3.0 P3
- Item 13: SQLite → Alembic — MVP3.0 P2
- Item 14: i18n — MVP3.0 P3

---

## 7. Open questions (to resolve before code starts)

- [x] ✅ **Item 4 / Option A vs B**: ~~in-place
  transcode (replaces original) or side-by-side (new
  file)?~~ **Resolved 2026-07-18**: side-by-side. See
  `doc/MVP2.1-Status.md` §2 "Why side-by-side not
  in-place" for the full rationale.
- [ ] **Worker pool / status API**: ship in 2.1.1, or
  defer to MVP3.0? *Proposed: ship in 2.1.1, the
  progress polling is needed for 100-video batches to
  feel responsive.*
- [x] ✅ **Plugin audit log**: ~~separate table or
  piggyback on the existing `Job` model?~~
  **Resolved 2026-07-18**: separate `plugin_runs`
  table. Cleaner schema, no overlap with
  upload/transcribe/generate jobs. See
  `app/models/plugin_run.py`.
- [ ] **THROTTLE default of 3**: confirm this is right
  for the user's M1 Max. If they have a 32-core machine,
  3 is too conservative. *Proposed: 3, with the env var
  to crank it up.*
