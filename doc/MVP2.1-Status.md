# MVP2.1 Status

> **TL;DR**: MVP2.0 is shipped on `main` (552 tests,
> 92% coverage, tag v2.0.8). MVP2.1 is a **focused
> 2-item release** on a **new branch `MVP2.1`**. MVP2.1.0
> (Plugin Tools tab + WebM→MP4) is shipped. MVP2.1.0.1
> (Tools tab UX fixes + Plugin worker pool) is the
> current commit. MVP2.1.1 (general worker pool for
> upload / transcribe / generate) is the next item.

---

## 1. Item status

| Version | Status | What it shipped | Branch |
|---|---|---|---|
| **2.1.0** | ✅ **Shipped** | Plugin Tools tab + WebM→MP4 | `MVP2.1` |
| **2.1.0.1** | ✅ **Shipped** (this commit) | Tools tab UX fixes (Re-Upload button visible after Run; no page reload after swap) + Plugin worker pool (limit=3, tab-close survives) | `MVP2.1` |
| **2.1.1** | 🟡 Not started | General worker pool for upload / transcribe / generate, throttle=3, configurable | `MVP2.1` (later) |
| MVP2.2 (paid) | ⏸️ Deferred 2-3 weeks | Stripe, hosted version, MLX as a paid add-on | `MVP2.2-paidVersion` (new branch) |
| MVP3.0 | 📋 Planned | OCR, cloud Whisper, soft-delete, etc. | `MVP3.0` (later) |

---

## 2. 2026-07-18 — Plugin Tools tab + WebM→MP4 (MVP2.1.0)

> User feedback (manualTodo [july14] #4 + the MVP2.0
> closure roadmap): "add a new function called transfer
> webm to MP4, but not as part of the main functions.
> ... should also have asc, and desc by name function
> etc." The "etc." was interpreted as "an extensible
> system for more actions on videos" — hence the Plugin
> Tools framework, not just a single WebM→MP4 button.

### What shipped

A new **🛠️ Tools** tab on the video page (next to
Summary, Flashcards, Quiz, Mindmap, Discuss, Chat). The
tab lists the available plugins from `PLUGIN_REGISTRY`
as cards. Each card has a Run button that POSTs to
`/api/plugins/{key}/run?video_id=<uuid>` and shows the
result inline below the button.

**v1 ships with one plugin: WebM → MP4.** The plugin
uses `ffmpeg` (system binary) to transcode the video
to H.264 + AAC, which is broadly compatible and
smaller than WebM at equivalent quality. The new file
is written **side-by-side** with the original (the
original WebM is never modified).

### Why "side-by-side" not "in-place"

The user explicitly chose side-by-side over in-place
during the design discussion. Reasoning:
- **Safer default**: if the MP4 is worse quality or
  the user regrets the transcode, the original is
  still there. They can delete it via the existing
  Delete Video button.
- **Easier to test**: side-by-side doesn't change the
  `Video.file_path` or `Video.status`, so no model
  migration and no transcribe-reset logic.
- **Future-friendly**: a "delete original" plugin can
  be added to the registry later (trivial — it's a
  one-function plugin that just `os.remove`s the
  source file).

In-place was originally proposed in `doc/MVP2.1-all.md`
§2. The implementation followed the user's
clarification during the MVP2.1 design discussion
(Tuesday, 2026-07-18).

### Design decisions (per user call)

- **Plugin = Python function** in
  `app/services/plugins.py`, registered in a dict
  (`PLUGIN_REGISTRY`). Plugins are NOT loaded from
  disk dynamically (no security audit needed, no
  install flow, no path-traversal risk). Adding a new
  plugin = adding one entry to the dict.
- **`PluginSpec` is a non-frozen dataclass** so tests
  can mock the `function` pointer without rebuilding
  the registry. (Originally was `frozen=True`; the
  test for exception swallowing required mockability,
  so I dropped `frozen=True` — the cost is that callers
  could mutate the registry at runtime, but nothing in
  the app does that.)
- **Run is synchronous** for v1 (the router blocks
  until the plugin finishes). For long plugins (e.g.
  a future "transcribe with subtitles" plugin), we'd
  swap to `BackgroundTasks` or the MVP2.1.1 worker
  pool. For WebM→MP4, the typical 1-hour video
  transcode is 2-5 minutes on a modern Mac, which is
  acceptable to block on.
- **ffmpeg detection at template-render time + at
  run-time**. The Run button is rendered disabled with
  a "Missing system dependency" warning if ffmpeg
  isn't on `$PATH`. The warning includes the exact
  install command for the user's OS.
- **Audit log per run** (`plugin_runs` table) — every
  invocation writes a row with `ok`, `message`,
  `output_path`, `extra_json`, `created_at`.
  CASCADE-deleted with the parent video.
- **404 (not 403) when accessing another user's
  video** — avoids leaking video IDs to attackers.
  Consistent with the rest of the app's
  auth-by-obscurity pattern.

### Files changed

| File | Lines | Why |
|---|---|---|
| `app/services/plugins.py` (new) | ~195 | PLUGIN_REGISTRY, PluginSpec, PluginResult, transcode_webm_to_mp4, is_ffmpeg_available |
| `app/models/plugin_run.py` (new) | ~60 | PluginRun audit log model |
| `app/models/__init__.py` | +3 | register PluginRun |
| `app/models/video.py` | +6 | add `plugin_runs` relationship to Video |
| `app/database.py` | +5 | import the new model so `create_all()` picks it up |
| `app/routers/plugins.py` (new) | ~115 | GET/POST /api/plugins router |
| `app/routers/frontend.py` | +25 | pass `available_plugins` to video page template |
| `app/main.py` | +2 | register the new router |
| `app/templates/video.html` | +75 | Tools tab button + content panel + `runPlugin()` JS function |

### Tests

- 552 → 583 (+31 new tests, all passing, 0 regressions
  in the existing 552)
- 4 new test files:
  - `tests/test_plugin_registry.py` (10 tests) —
    registry shape, key uniqueness, URL-safety,
    ffmpeg detection
  - `tests/test_webm_to_mp4_plugin.py` (8 tests) —
    ffmpeg-missing, source-missing, ffmpeg-error,
    timeout, real ffmpeg happy path, audit log row,
    unknown-key audit, exception swallow
  - `tests/test_tools_tab_rendering.py` (7 tests) —
    Tools tab button, content panel, plugin card,
    Run button, ffmpeg-missing disabled state,
    `runPlugin()` JS function presence
  - `tests/test_plugin_endpoints.py` (6 tests) — list
    endpoint, run endpoint, 404s, audit log row, get
    run by id

### Migration notes

No DB migration needed. `plugin_runs` is a new table
(created automatically by `Base.metadata.create_all()`
on first startup). No data needs to be backfilled.

### What's deferred from 2.1.0 to future versions

- **UI: show the audit log per video** ("Last transcode:
  2 hours ago, 1.2 GB MP4 written"). The data is
  captured in the DB; just needs a `plugin_runs`
  section on the video page. Deferred to 2.1.2 or 2.2.
- **"Delete original" plugin** (the follow-up the
  side-by-side decision implies). Trivial to add; just
  a one-function plugin that `os.remove`s the source
  file. Deferred to 2.1.2 or 2.2.
- **BackgroundTools vs sync** — for the WebM→MP4 case,
  sync is fine (1-5 min). For a future "re-transcribe
  with new model" plugin that takes 30+ min, we'd want
  the worker pool (2.1.1).

---

## 3. Next up: MVP2.1.1 — Worker pool

[Tracked in `doc/MVP2.1-all.md` §3.]

`concurrent.futures.ThreadPoolExecutor` with
`max_workers=3` (configurable via `WORKER_POOL_SIZE`
env var). Replaces `BackgroundTasks` in
`app/routers/videos.py` and `app/routers/generation.py`.
The Plugins router (just shipped in 2.1.0) will be
refactored to use the pool instead of sync runs.

**Estimated:** 1-2 weeks (option A: 1-2 days for the
no-status-API version).

---

## 4. What's NOT in MVP2.1 (deferred)

- **MVP2.2-paidVersion** — Stripe, hosted version,
  Discord community, MLX as a paid add-on. Deferred
  2-3 weeks per the user's planning discussion
  (2026-07-16).
- **MVP3.0** — OCR (separate paid repo), cloud
  Whisper API, soft-delete, etc. See
  `doc/MVP3.0-Status.md` for the full list.

---

## 5. Open questions (to resolve before 2.1.1 starts)

- [ ] **Worker pool scope** — full status-polling API
  or just the "swap BackgroundTasks for ThreadPoolExecutor"
  version? User's call.
- [ ] **Plugin audit log UI** — show it on the video
  page in 2.1.1, or defer to 2.2 alongside the paid
  features?
- [ ] **Plugins folder in sidebar** — currently the
  plugin run history is per-video. A user with 100
  videos might want a global "all my plugin runs" view
  in the sidebar. Defer to 2.2.

---

## 6. 2026-07-19 — Tools tab UX fixes + Plugin worker pool (MVP2.1.0.1)

**TL;DR**: Three user-reported UX bugs in the Tools tab
("Re-Upload" button not visible after Run; player
shows only "0:02" after swap; closing the tab kills the
transcode) + a backend architectural change to fix the
latter (plugin worker pool with bounded concurrency).

### What changed

| Area | Change | Files |
|---|---|---|
| **UX: Re-Upload button** | JS `refreshLastRun()` template now includes the "Re-Upload with MP4" button (was missing — only "Open in Finder" was in the JS version). Extracted as `renderSwapButton()` helper so the two paths (server-render + JS-update) stay in lockstep. | `app/templates/video.html` |
| **UX: videoStatus exposed to JS** | New `const videoStatus = '{{ video.status }}';` so the JS template can mirror the server-side "disabled when video is not ready" condition. | `app/templates/video.html` |
| **UX: no page reload after swap** | `performSwap()` now sets `videoEl.src = /api/videos/.../file?v=${Date.now()}; videoEl.load();` instead of `setTimeout(location.reload, 800)`. The cache-bust query param forces a fresh fetch (no bfcache stale state); `load()` re-reads metadata so duration + 3-dots menu render correctly. | `app/templates/video.html` |
| **Backend: Plugin worker pool** | New `app/workers/plugin_pool.py` with `asyncio.Queue` + `asyncio.Semaphore(3)`. The `POST /api/plugins/{name}/run` endpoint now returns 202 + `{run_id, status: "queued"}` in <50ms. UI polls `GET /api/plugins/runs/{id}` every 1.5s. Closing the tab no longer cancels the job. | `app/workers/plugin_pool.py` (NEW), `app/routers/plugins.py`, `app/templates/video.html` (runPlugin JS) |
| **DB: PluginRun.status** | New `status` column on `plugin_runs` (additive migration; legacy rows backfilled to `'done'`). Values: `queued` / `running` / `done` / `failed`. Exposed in `/api/plugins/runs/{id}` and `/api/plugins/runs/by-video/{id}`. | `app/models/plugin_run.py`, `app/database.py` (migration) |
| **Service: split run_plugin** | Refactored `app/services/plugins.py:run_plugin()` into a thin public wrapper around `_run_plugin_and_create_row()` (the internal helper). The wrapper keeps backward compat; the helper is reused by the worker. | `app/services/plugins.py` |
| **Lifecycle: start/stop pool** | `app/main.py` lifespan handler calls `plugin_pool.start()` on app startup and `plugin_pool.stop(timeout=30.0)` on shutdown. | `app/main.py` |
| **Tests: conftest patching** | `tests/conftest.py` patches `app.workers.plugin_pool.SessionLocal` (in addition to the existing patches for videos / generation). Also enables `plugin_pool.synchronous_mode = True` for tests (so they don't have to poll for the worker). Resets pool state between tests. | `tests/conftest.py` |
| **Tests: plugin worker tests** | NEW `tests/test_plugin_worker.py` (8 tests) covering status field transitions, 202 response, by-video endpoint, tab-close survival, pool stats, 404, no duplicate rows in sync mode. | `tests/test_plugin_worker.py` (NEW) |
| **Tests: tools tab UI tests** | +3 tests in `tests/test_tools_tab_rendering.py` covering the new JS `renderSwapButton()` helper, `videoStatus` exposure, and `performSwap()` using `videoEl.src + videoEl.load()`. | `tests/test_tools_tab_rendering.py` |
| **Tests: plugin endpoint updates** | 2 existing tests in `tests/test_plugin_endpoints.py` updated for the 202 + status response shape. | `tests/test_plugin_endpoints.py` |

### Test count

- **Before**: 603 passing, 1 pre-existing failure (whisper picker)
- **After**: 614 passing, same 1 pre-existing failure
- **+11 new tests, 0 regressions**

### Why a worker pool (not BackgroundTasks)?

The existing FastAPI `BackgroundTasks` mechanism is
**per-request**: the request must stay open for the
background work to be tracked. For a 5+ minute plugin
run, the user closes the tab → the request is cancelled
→ the background task is killed. A dedicated
`asyncio.Queue`-based pool with its own worker task
**survives the request lifecycle**.

### Why limit=3?

ffmpeg is CPU-bound; 3 parallel runs can use 3 cores
without thrashing. Configurable via the
`PluginPool(limit=...)` constructor argument. The user
chose 3 after considering: serial (limit=1, safest but
slowest wall-clock), 2, 3, or no limit (would thrash).
3 is a good balance.

### Why `synchronous_mode = True` for tests?

The worker task is bound to the TestClient's anyio
event loop, which closes when the TestClient exits.
Between tests, the pool's worker task is on a closed
loop, which makes cross-test polling unreliable.
`PluginPool.synchronous_mode = True` (set by the
`client` fixture in conftest.py) makes `submit()` run
the plugin inline + update the row before returning.
Tests assert on the result without polling, and
cross-test pollution is impossible.

### Open questions resolved

- ✅ **Worker pool scope** — separate pool per category
  (plugin only for v1, full pool in 2.1.1). The user
  explicitly preferred separate pools with their own
  limits.
- ✅ **Plugin audit log UI** — the per-video "Last run"
  line (in 2.1.0) is the user-facing view. A global
  "all my plugin runs" view is deferred to 2.2.
- ✅ **Why 2.1.0.1 instead of 2.1.1** — the UX fixes
  + plugin pool are a focused ~500-line change. The
  general worker pool (upload / transcribe / generate)
  is a bigger refactor that doesn't ship user-visible
  features on its own.
