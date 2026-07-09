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
| 11 | **Celery + Redis task queue** | ✅ In, but **after** #1 lands | Solves "no parallelism" + "jobs lost on restart" |
| 12 | **S3 / MinIO for storage** | ⏭️ Deferred to MVP3 | Per user call |
| 13 | **Auto-download recording: separate "first video" vs "rest" modes** | ⏭️ Deferred | Per user call |
| 14 | **Public API (OpenAPI) + API key auth + rate limiting** | ⏭️ Deferred | Per user call |
| 15 | **MCP server** | ⏭️ Deferred | Per user call |
| 16 | **OAuth2 + Stripe (paid memberships)** | ✅ In, **low priority** | — |
| 17 | **Docker + Kubernetes deployment** | ⏭️ Deferred | Per user call |
| 18 | **Smart/Always transcript scroll modes** (restore as 2 extra options alongside current Top-anchor default) | ⏭️ Deferred to MVP3 | 4 open design Qs → see [Appendix A](#appendix-a--smartalways-modes-deferred-design-questions) |

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
- #9 — Mindmap node count for long videos (repro / data-gathering commit first)
- #10 — Alembic migrations (schema-versioning safety net)
- #11 — Celery + Redis task queue (parallelism + restart-safety for transcribe jobs)
- #6 — Default output language for Chinese (tied to #9)

## Appendix A — Smart/Always modes: deferred design questions

> **Context:** MVP2.0 shipped the "top-anchor" transcript follow mode as the single default (active line always pinned to the top of the panel). The old MVP1.1 smart/always modes were removed. User asked to bring them back as optional extras in MVP3. Four design questions must be answered before that work starts.

| # | Question | Options / Notes |
|---|---|---|
| A1 | **Mode names in the dropdown?** | Suggestion: "Top (default)" / "Smart" / "Center". Or keep original names "Smart" and "Always". Needs user decision. |
| A2 | **Persist the selected mode in localStorage?** | Old code persisted per-user-email (needed the `x-user-email` meta we removed). Options: (a) persist by a fixed key, no per-user namespacing; (b) no persistence at all — user re-picks each load; (c) re-add the email meta tag for this purpose only. |
| A3 | **Dropdown placement?** | Old location: "Follow: [dropdown]" inline next to "📜 Transcript" heading. Same position, or somewhere else? |
| A4 | **Both restored modes must use `getBoundingClientRect` (not `offsetTop`)** | Not a question — a constraint. The old `scrollContainerToCenter` had the same `offsetTop`-relative-to-body bug as the top-anchor mode. Any MVP3 restore must rewrite both scroll helpers with `getBoundingClientRect`. Implementation is straightforward once A1–A3 are decided. |

**Status:** Deferred to MVP3. When MVP3 planning starts, answer A1–A3 and implement A4.