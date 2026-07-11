# MVP 2.0

## functions

| # | Title | Status for MVP2 | Notes |
|---|---|---|---|
| 1 | **Auto-transcribe + auto-generate on upload** | ✅ Done + pushed (`7e70fe3`, `b4d612f`) | Chains `upload → transcribe → generate` as a FastAPI BackgroundTask; `status='queued'` is set at upload so the UI can poll `/status` immediately |
| 2 | **Single "top-anchor" transcript follow mode** | ✅ Done + pushed (`05525ee`) | Top-anchor is the default; smart/always deferred to MVP3 → see [#18](#18-smartalways-transcript-modes) |
| 3 | **Multi-file / non-blocking upload w/ 2 GB per-file cap** | ✅ Done + pushed (`7e70fe3`, `b4d612f`) | Per-file 2 GB cap with partial success; UI alerts queued vs skipped counts |
| 4 | **Video sort by leading filename number on course page** | ✅ Done + pushed (`dd35bc1`) | Client-side sort, default asc, per-section `↑ asc / ↓ desc` button, choice persisted in localStorage; unnumbered videos sort last in asc / first in desc |
| 5 | **YouTube / URL downloader (yt-dlp)** | ❌ Removed from MVP2 | — |
| 6 | **Default output language for Chinese (S vs T)** | ✅ In, but in a later wave | Tied to #9; not blocking |
| 7 | **Session-expiry redirect on protected video pages** | ✅ Done + pushed (`814a98a`, `2b40fc9`, `67d980b`) | One of the small UX items |
| 8 | **Persisted "Generate Materials" state on re-login** | ✅ Done | — |
| 9 | **Prompt tuning: stable mindmap node count for long videos** | ✅ In, but **first commit = data-gathering** (repro the issue, count nodes/min across video lengths) before any prompt change | Assumption may be right but unverified |
| 10 | **Alembic migrations** (PostgreSQL part deferred) | ✅ Alembic in MVP2.0; PostgreSQL → MVP3 | Alembic = schema-versioning safety net we need before any #1-scale changes |
| 11 | **Celery + Redis task queue** | ✅ In, **next-up** (after #9 repro, before #19) | Solves "no parallelism" + "jobs lost on restart"; #1 has now landed so the queue is the next bottleneck users will feel |
| 12 | **S3 / MinIO for storage** | ⏭️ Deferred to MVP3 | Per user call |
| 13 | **Auto-download recording: separate "first video" vs "rest" modes** | ⏭️ Deferred | Per user call |
| 14 | **Public API (OpenAPI) + API key auth + rate limiting** | ⏭️ Deferred | Per user call |
| 15 | **MCP server** | ⏭️ Deferred | Per user call |
| 16 | **OAuth2 + Stripe (paid memberships)** | ✅ In, **low priority** | — |
| 17 | **Docker + Kubernetes deployment** | ⏭️ Deferred | Per user call |
| 18 | **Smart/Always transcript scroll modes** (restore as 2 extra options alongside current Top-anchor default) | ⏭️ Deferred to MVP3 | 4 open design Qs → see [Appendix A](#appendix-a--smartalways-modes-deferred-design-questions) |
| 19 | **Duplicate video detection on upload (hash-based + confirm UI)** | 🆕 Open (planned 2026-07-10) | sha256 of file → check `(user_id, content_hash)` index → if match, return 409 with `{duplicate: true, existing: {...}}` so the UI can offer "Skip" / "Upload anyway". No re-transcribe, no disk waste. See [Appendix B](#appendix-b--duplicate-detection-design-questions) |

## design

### Why we need each non-obvious item

- **#10 — Alembic (the bigger reason is schema-versioning, not PostgreSQL itself)**
  - Today schema is created with `Base.metadata.create_all()` at startup ([app/database.py](app/database.py)). Adding a column is silently applied on next run — no record, no rollback. Fine for a single dev on a single machine. The moment we have any of: a cloud deploy, a second contributor, a non-trivial data backfill, or a rollback path — Alembic is the difference between "we shipped it" and "we shipped it and prod is broken with no record of what changed."
  - PostgreSQL is the operational reason: SQLite's "single writer at a time" is fine for one user, will deadlock with real concurrency. But that part is deferred to MVP3.

- **#11 — Celery + Redis**
  - Today `app/jobs.py` is an **in-process Python dict** keyed by `(video_id, job_type)`. Two concrete problems:
    1. **No parallelism.** Whisper is single-threaded CPU-bound (~5-15 min on `medium`); video B's transcribe request sits in the queue behind video A on the same Python process. With Celery + multiple worker processes, you can transcribe N videos in parallel.
    2. **Jobs are lost on restart.** Kill uvicorn → all in-flight jobs vanish from the dict. User sees "running" forever. With Redis/Celery, the queue persists.
  - Not strictly needed for "single user on one laptop." Needed the moment #1 ships, because users will start kicking off many transcribe jobs in a row and the sequential single-process queue will become obvious.

- **#9 — Mindmap node count for long videos (repro first, fix second)**
  - The user's data point: 4 nodes at 2 min, <4 nodes at 12 min for the same first 2 min. Three plausible hypotheses, need to look at [app/services/llm.py](app/services/llm.py) prompt + actual outputs to know which is the right fix:
    - **A.** Prompt says "summarize into N mindmap nodes" and the LLM interprets N as a fixed *total* count, not per-minute density → fewer nodes/min for longer videos. Fix: change prompt to "≥1 node per major section/topic, minimum X per 5 minutes."
    - **B.** LLM truncates output when the response would exceed some token budget. Fix: chunk transcript by time window, generate mindmap per chunk, merge.
    - **C.** Whisper segments for the first 2 min are identical, but the prompt is given the whole 12-min transcript and the LLM decides to skip nodes for sections it deems "less novel." Fix: prompt tuning to force uniform density.
  - First commit is a **repro / data-gathering** one — generate mindmaps for 2, 5, 12, 30 min versions of the same source and count nodes per minute. Then we know what to fix.

## discussion:
- session timeout details discussion
  - **decision (2026-07-09):** when the `fb_token` cookie is expired/invalid, the user is redirected to `/?session=expired` from any protected SSR route (`/`, `/course/{id}`, `/video/{id}`, `/chat-history`). API routes (`/api/*`) keep returning 401 as before. The "session expired" warning is shown as a toast on the dashboard (option **B**).
  - **rejected — A (top banner):** would push the page layout down on arrival; overkill for a non-actionable info ("you need to sign in" is the action, the banner isn't).
  - **rejected — C (inline next to sign-in card):** redundant with the existing sign-in affordance; user has to look at a specific spot to see the message.

### Status log

> **Snapshot from earlier in the day (2026-07-09) — kept for commit-hash traceability.** The current status of every item is in the **Milestones** table at the top of this document. If you're reading this section for "is it done?", look at Milestones, not here.

- **#2 — Single "top-anchor" transcript follow mode: implementation + tests done, awaiting user verification.**
  - Branch: `MVP2.0` (not yet pushed; user wants to review the diff before push)
  - Commits (newest → oldest):
    - `f55c6c6` — *part B, tests + fix* — rewrite of `tests/test_transcript_follow.mjs` (17 tests via a tiny DOM shim: pure helpers, source-level guards, public surface locks, integration) and `tests/test_transcript_follow.py`; updates to `tests/test_frontend.py` and `tests/test_main.py` to drop the dropdown/meta expectations and add negative asserts (the old API must NOT reappear in the served JS); a `forceScroll` parameter added to the JS while writing the integration tests (mouseleave was leaving the panel unscrolled when the active line hadn't changed).
    - `e5ca10f` — *part A, implementation* — `app/static/js/transcript-follow.js` rewritten to the single top-anchor mode (rAF-throttled scroll, `seeked` listener, hover-to-pause via `mouseenter`/`mouseleave`, no setMode/getMode/storageKey); dropdown removed from `app/templates/video.html`; `x-user-email` meta removed from `app/templates/base.html`; comment-only update to `app/static/css/transcript-follow.css`.
  - Test results: 320/320 passing (was 319 after #7; +1 from the merged frontend tests, +17 from the .mjs suite).
  - Public API after #2: `window.TranscriptFollow = { init({container, video, segmentsProvider}), destroy() }` + `_internals.findActiveSegment`. No modes, no localStorage, no per-user meta.

- **#7 — Session-expiry redirect: implementation + tests done, awaiting user verification.**
  - Branch: `MVP2.0` (not yet pushed; user wants to review the diff before push)
  - Commits (newest → oldest, on `MVP2.0` ahead of `origin/main` by 3):
    - `67d980b` — *part C, fix tests* — broader `Exception` catch in `SessionExpiryMiddleware` (real Firebase SDK raises `FirebaseError`, not `ValueError`) + middleware order swap in `app/main.py` so `SecurityHeadersMiddleware` wraps the 302 redirect.
    - `2b40fc9` — *part B, tests* — `tests/test_session_expiry_middleware.py` (26 tests, 100% coverage on `app/middleware_session.py`).
    - `814a98a` — *part A, implementation* — new `app/middleware_session.py`; `app/main.py` mount; `app/templates/base.html` toast trigger + `showToast` move; `app/templates/video.html` local `showToast` removal.
  - Test results: 319/319 passing; project overall coverage 87% (unchanged from baseline; new file at 100%).
  - Verified behaviors: protected SSR routes redirect on bad cookie; anonymous visits stay put; `/api/*` keeps 401 JSON; `/login`, `/static/*`, `/api/auth/session` always reachable; 302 carries CSP + X-Frame-Options + X-Content-Type-Options; toast fires once and self-clears via `history.replaceState`.


### Suggested MVP2.0 phasing

| Wave | Items | Theme | Status (2026-07-09) |
|---|---|---|---|
| **MVP2.0.0 — UX polish** (small, low-risk) | #7 | ~half day | ✅ **Done** (3 commits, 26 tests) |
| **MVP2.0.0a — Transcript follow rewrite** (small) | #2 | JS-only; sibling of MVP2.0.0 | ✅ **Done** (3 commits, 17 .mjs tests + regression guards) |
| **MVP2.0.1 — Auto-pipeline** *(in progress)* | #1, #2, #3 ✅; #9, #10 still open | Biggest UX win is #1 | **Wave 1 done** (auto-pipeline + bulk upload). #9 (repro) and #10 (Alembic) still to land. |
| **MVP2.0.2 — i18n + prompt tuning** | #6, #9 (now with the data we gathered) | Polishes the auto-pipeline | ⏳ Blocked on #9 repro |
| **MVP2.0.3 — Queue + payments** | #11, #16 (low-priority) | Infrastructure for scale + monetization | ⏳ Future |

## questions

### Q1 — Why do we need Alembic / PostgreSQL (#10)?

**A.** Alembic is the bigger reason than PostgreSQL itself. Today schema is created with `Base.metadata.create_all()` at startup. That means if I add a column to a model, the change is silently applied on the next run — no record, no rollback. For MVP1 that's fine because you're a single dev on a single machine. For MVP2 the moment we have any of: a cloud deploy, a second contributor, a schema migration with a non-trivial data backfill, or a rollback path — Alembic is what makes the difference between "we shipped it" and "we shipped it and now prod is broken and we have no record of what changed." PostgreSQL is the operational reason: SQLite's "single writer at a time" is fine for one user, will deadlock the moment we add real concurrency (the `Mvp1.0-postRelease-critical review.md` calls this out directly). **Resolution:** Alembic in MVP2.0, PostgreSQL → MVP3.

### Q2 — Why do we need Celery + Redis (#11)?

**A.** Today `app/jobs.py` is an **in-process Python dict** keyed by `(video_id, job_type)`. Two concrete problems: (1) **No parallelism** — if video A is transcribing, video B's request sits in the queue behind it on the same Python process; with Celery + multiple worker processes, you can transcribe N videos in parallel. (2) **Jobs are lost on restart** — kill uvicorn → all in-flight jobs vanish; the user sees "running" forever; with Redis/Celery, the queue persists. You don't *need* it for MVP2's "single user on one laptop" case. You *do* need it the moment #1 (auto-transcribe on upload) ships, because users will start kicking off many transcribe jobs in a row and the sequential single-process queue will become obvious. **Resolution:** Keep in MVP2.0, but schedule it **after** #1 lands.

### Q3 — Is the #9 mindmap hypothesis actually correct?

**A.** Unknown. The user's observation is plausible (4 nodes @ 2 min, <4 @ 12 min for the same first 2 min) but we have three competing hypotheses (fixed-total-N, token-budget truncation, novelty-skipping), and the right fix depends on which one is true. **Resolution:** First commit is a **repro / data-gathering** task — generate mindmaps for 2, 5, 12, 30 min versions of the same source and count nodes per minute — *before* touching the prompt.

## Milestones:

> **Status snapshot (updated 2026-07-11).** The TL;DR / "open items for tomorrow" sections below were written on 2026-07-09 and are now **stale** — that day already happened and we shipped MVP2.0.0a + MVP2.0.1 wave 1 + wave 2. For the live picture, see [`doc/MVP2.0-Status.md`](MVP2.0-Status.md).

### TL;DR for tomorrow's session (2026-07-10)

**#19 — Duplicate video detection.** Discuss B1–B5 in [Appendix B](#appendix-b--duplicate-detection-design-questions) before coding. My default answers are there; user picks. Then implement: hash-on-upload + 409 + (auto-skip on bulk / confirm on single).

### 2026-07-09 — MVP2.0.0a + MVP2.0.1 wave 1 closed out

End-of-day status, all work on branch `MVP2.0` (10 commits ahead of `main`, all pushed).

| Item | Status | Commit(s) | Verified? |
|---|---|---|---|
| #2 — Single "top-anchor" transcript follow mode | ✅ Done + pushed | `e5ca10f`, `f55c6c6`, `05525ee` | ✅ User verified live |
| #7 — Session-expiry redirect on protected SSR routes | ✅ Done + pushed | `814a98a`, `2b40fc9`, `67d980b` | ✅ User verified live |
| #1 — Auto-transcribe + auto-generate on upload | ✅ Done + pushed | `7e70fe3`, `b4d612f` | ✅ User verified live (4-video bulk run completed) |
| #3 — Multi-file / non-blocking upload w/ 2 GB cap | ✅ Done + pushed | `7e70fe3`, `b4d612f` | ✅ User verified live (4 files → 4 ready) |
| **#4 — Filename numbering (natural sort)** *(new today)* | ✅ Done + pushed | `dd35bc1` | ✅ Per user request — default asc, sort button on each section |
| **Bulk-upload route shadowing fix** *(discovered live today)* | ✅ Done + pushed | `dbeed50` | ✅ Verified via curl + user re-test |
| **LLM JSON parser hardening** *(discovered live today)* | ✅ Done + pushed | `e8de51b` | ✅ Verified by re-running failed video 1 → all 4 ready |

**Test results:** 347/347 passing, **87% overall coverage**.
- New today: +17 tests (was 330, +2 route-shadowing regression guards, +5 LLM parser strategies, +10 natural_sort_key).
- 100% coverage on `app/models/video.py`, `app/services/llm.py`, `app/middleware_session.py`, `app/services/markdown.py`, all `app/auth/*`, all `app/middleware*`, all `app/services/__init__`.
- Lower-coverage modules are background-worker code paths (`routers/generation.py` 47%, `routers/videos.py` 71%) — deliberately integration-tested, not unit-tested, because they exercise real Whisper + Ollama.

**Postmortems added to `doc/Blockers.md`:**
- ✅ Transcript scroll bug (`offsetTop` vs `getBoundingClientRect`) — committed 2026-07-09
- ✅ Bulk-upload route shadowing (FastAPI route order vs `/{param}/...`) — committed 2026-07-09

**Design doc updates (this commit):**
- Row #1: "✅ In, top priority" → "✅ Done + pushed" — committed 2026-07-09
- Row #2: confirmed done + pushed (`05525ee`) — already updated previously
- Row #3: "✅ In" → "✅ Done + pushed" — committed 2026-07-09
- Row #4 (sort) — added today
- Row #7: confirmed done + pushed — already updated previously
- §4 AI Pipeline in `doc/design.md` — pending (still describes manual click-to-transcribe)
- `doc/MVP1.0-PostRelease.md` row #6 — pending (still says `status='pending'`, now `'queued'`)

### Open items for tomorrow

- Doc cleanup pass (the 3 doc items above)
- #19 — Duplicate video detection (hash-based) + confirm UI — **new, user-asked**
  - **Discuss 5 design questions B1–B5 in Appendix B first** — see the "Recommendation" block for my default answers; user picks final
- #9 — Mindmap node count for long videos (repro / data-gathering commit first)
- #10 — Alembic migrations (schema-versioning safety net)
- #11 — Celery + Redis task queue (parallelism + restart-safety for transcribe jobs)
- #6 — Default output language for Chinese (tied to #9)

### 2026-07-10 — MVP2.0.1 wave 2: retry + export

End-of-day status on branch `MVP2.0` (16 commits ahead of `main`, all pushed).

| Item | Status | Commit(s) | Verified? |
|---|---|---|---|
| **Retry script for failed generate jobs** | ✅ Done + pushed | `f37f7a0` | ✅ 13/13 videos recovered in dry run |
| **Transcript export endpoint** (`.md` / `.json` / `.txt`) | ✅ Done + pushed | `a1235b2` | ✅ Endpoint tested via curl |
| **Retry-all-failed button on section header** | ✅ Done + pushed | `3bb256b` | ✅ Roundtripped via the JSON API |
| **Download transcript button on video page** | ✅ Done + pushed | `72ae0bc` | ✅ User verified live |
| **#19 — Duplicate video detection (B1–B5)** | ⏳ Pending user decisions | — | Discussion Qs still in Appendix B |
| **#10 — Alembic migrations** | ⏳ Not started | — | — |

**Test results:** 394/394 passing (+47 from prior day, was 347).
- New: 13 retry-helper tests, 19 transcript-export tests, 3 route-shadowing regression guards, plus the 12 v2 endpoint tests.

**Postmortems added to `doc/Blockers.md`:** none new (the 3 from 2026-07-09 still cover everything encountered).

### 2026-07-11 — MVP2.0.1 wave 2 fix + doc pass

Morning of 2026-07-11, on branch `MVP2.0` (20 commits ahead of `main`, all pushed).

| Item | Status | Commit(s) | Verified? |
|---|---|---|---|
| **"Retry 1 failed" button does nothing** *(user-reported live today)* | ✅ Fixed + pushed | `162c85d` | ✅ Endpoint partition logic tested; user verified live (toast appears) |
| **Blockers postmortem for retry button** | ✅ Done + pushed | `ee466ab` | — |
| **HowToStart doc + stop.sh / status.sh helpers** | ✅ Done + pushed | `6ce7571` | ✅ status.sh tested on the live server |
| **MVP2.0-Status.md** *(NEW single-page status)* | ✅ Done + pushed | this commit | — |
| **CHANGELOG.md MVP2.0 entry** | ✅ Done + pushed | this commit | — |
| **Readme.md updated to MVP2.0 stats** | ✅ Done + pushed | this commit | — |
| **MVP1.0-PostRelease.md row #6** | ⏳ Still says `status='pending'`, now `'queued'` | — | — |
| **doc/design.md §4 AI Pipeline** | ⏳ Still describes manual click-to-transcribe | — | — |

**Test results:** 396/396 passing (+2 for the new transcribe-failure retry coverage).

### Open items (current, 2026-07-11)

- **#5 — Delete video button** *(user-asked in `manualTodo.txt`)* — design exists in `Todo.md` #10. Need it to clean up the 0-byte video that's still sitting in section 3.
- **#19 — Duplicate video detection** (B1–B5 in Appendix B) — user needs to sign off on the 5 design questions, then we can build.
- **#9 — Mindmap node count for long videos** — repro / data-gathering first.
- **#10 — Alembic migrations** — schema-versioning safety net.
- **#11 — Celery + Redis** — restart-safety + parallelism for transcribe jobs.
- **#6 — Default output language for Chinese** — tied to #9.
- **§4 AI Pipeline in `doc/design.md`** — still describes manual click-to-transcribe, needs to be updated to reflect the auto-pipeline.
- **`doc/MVP1.0-PostRelease.md` row #6** — still says `status='pending'`, now `'queued'`.

## Appendix A — Smart/Always modes: deferred design questions

> **Context:** MVP2.0 shipped the "top-anchor" transcript follow mode as the single default (active line always pinned to the top of the panel). The old MVP1.1 smart/always modes were removed. User asked to bring them back as optional extras in MVP3. Four design questions must be answered before that work starts.

| # | Question | Options / Notes |
|---|---|---|
| A1 | **Mode names in the dropdown?** | Suggestion: "Top (default)" / "Smart" / "Center". Or keep original names "Smart" and "Always". Needs user decision. |
| A2 | **Persist the selected mode in localStorage?** | Old code persisted per-user-email (needed the `x-user-email` meta we removed). Options: (a) persist by a fixed key, no per-user namespacing; (b) no persistence at all — user re-picks each load; (c) re-add the email meta tag for this purpose only. |
| A3 | **Dropdown placement?** | Old location: "Follow: [dropdown]" inline next to "📜 Transcript" heading. Same position, or somewhere else? |
| A4 | **Both restored modes must use `getBoundingClientRect` (not `offsetTop`)** | Not a question — a constraint. The old `scrollContainerToCenter` had the same `offsetTop`-relative-to-body bug as the top-anchor mode. Any MVP3 restore must rewrite both scroll helpers with `getBoundingClientRect`. Implementation is straightforward once A1–A3 are decided. |

**Status:** Deferred to MVP3. When MVP3 planning starts, answer A1–A3 and implement A4.
## Appendix B — Duplicate detection: design questions

> **Context:** User noticed that re-uploading the same file creates a second `Video` row + second file on disk + second auto-pipeline (so 2× Whisper + 2× Ollama). They asked for "tell me if I accidentally uploaded an existing video, and ask if I still want to upload." Three design questions must be answered before implementation starts.

| # | Question | Options / Notes |
|---|---|---|
| B1 | **Match granularity?** | (a) **Exact byte match (sha256)** — catches "I uploaded the same file twice", fast (~1s for 100MB streamed). Recommended default. (b) **Same `(section_id, filename)`** — free, no hashing, but breaks the moment a user renames the file. (c) **Perceptual hash** — catches "I re-encoded the same video" but is much more code (ffmpeg + phash + tolerance threshold). Defer to MVP3. |
| B2 | **What counts as "the same user" for matching?** | (a) Per-user (recommended) — same file uploaded to two different user accounts is fine, just don't dupe within a user's own library. Requires joining `videos → sections → courses` to filter by `courses.user_id`. (b) Global — any user uploading the same hash is rejected. Avoid unless there are real privacy concerns. |
| B3 | **Confirm-before-upload UI flow?** | (a) **Auto-skip on bulk, confirm-on-single** — bulk upload is more repetitive so auto-skip is the right default; single upload shows "looks like a dupe, upload anyway?" (b) **Always confirm** — consistent UX, more clicks. (c) **Always skip silently** — too aggressive; user loses work. Needs user decision. |
| B4 | **Same content, different section — dupe or not?** | Recommend: **not a dupe.** The user's intent may be to put the same intro video in two different modules. The hash match is only a dupe within `(user_id, section_id, content_hash)`. |
| B5 | **Hash storage cost** | sha256 of a 500MB file is 32 bytes per row. With 1000 videos, 32KB total. Trivial. Just store on `videos.content_hash String(64) NOT NULL` (NOT NULL after a one-time backfill of existing rows with NULL → some sentinel, or just allow NULL until re-uploaded). |

**Recommendation** (for tomorrow's commit):
- B1 = (a) sha256, streaming
- B2 = (a) per-user
- B3 = (a) auto-skip on bulk, confirm on single
- B4 = different section = not a dupe
- B5 = String(64), allow NULL for existing rows

**Files to touch:**
- `app/models/video.py` — add `content_hash: Mapped[str | None]` column
- `app/routers/videos.py` — compute hash during the save (read in 4MB chunks to avoid loading huge files into memory), check existing `(user_id, content_hash)` before INSERT
- `app/templates/course.html` + `app/templates/dashboard.html` — handle 409 in the upload JS, show confirm prompt
- `tests/test_videos.py` — fixture uploads a known file, second upload returns 409 with existing metadata

**Status:** Awaiting user confirmation on B1–B5 before implementation. Default plan above.
