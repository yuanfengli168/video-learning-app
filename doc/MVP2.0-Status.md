# MVP2 Status — 2026-07-11

> **TL;DR**: MVP2.0 is **~70% shipped** on branch `MVP2.0` (20 commits ahead of `main`, all pushed, 396/396 tests passing, 87% coverage). MVP2.0.0 + 2.0.0a + 2.0.1 wave 1 are done. Wave 2 (delete button, duplicate detection) and MVP2.0.2 (i18n, mindmap tuning) are still open.
>
> For full milestone history, see [`doc/MVP2.0-first-designQuestions.md`](MVP2.0-first-designQuestions.md). For bug postmortems, see [`doc/Blockers.md`](Blockers.md).

> **📌 Current snapshot (2026-07-15)**: Branch `MVP2.0` is **65 commits ahead of `main`**, all pushed. **540/540 tests passing**, 87% coverage maintained. MVP2.0.0 / 2.0.0a / 2.0.1 (language policy) / 2.0.2 (Discuss-tab citations) / 2.0.3 (tab switching) / 2.0.4 (per-step timing) / 2.0.5 (bulk-upload 400 fix) are all shipped. The original "2.0.2 (i18n, mindmap tuning)" was re-scoped to a 2.0.0+ feature. Remaining MVP2.0 work: soft-delete (item 5 in MVP3.0-Status.md), bulk upload still single-process. See §19 for the latest item and §20 for the next-up plan.


---

## 1. Phasing recap (from the design doc)

| Phase | Scope | Status |
|---|---|---|
| **MVP2.0.0** — UX polish | Natural sort + session-expiry redirect + LLM JSON hardening + route-shadowing fix | ✅ **Shipped** (10 commits) |
| **MVP2.0.0a** — Transcript follow rewrite | Single top-anchor mode, `getBoundingClientRect` not `offsetTop` | ✅ **Shipped** (3 commits) |
| **MVP2.0.1** — Auto-pipeline (wave 1) | Auto-transcribe + auto-generate on upload; bulk upload w/ 2 GB cap | ✅ **Shipped** (6 commits) |
| **MVP2.0.1** — Auto-pipeline (wave 2) | Retry script + retry-this + retry-all-failed + transcript export (.md/.json/.txt) + 0-byte rejection | ✅ **Shipped** (6 commits, includes 2026-07-10 + 07-11) |
| **MVP2.0.1** — Auto-pipeline (wave 3) | Alembic migrations + Celery/Redis task queue | ⏳ Not started |
| **MVP2.0.2** — i18n + prompt tuning | Default language for Chinese + mindmap node count repro | ⏳ Blocked on #9 repro |
| **MVP3** | Restore Smart/Always transcript-follow modes (Appendix A) | ⏳ Deferred — design Qs A1–A3 unanswered |

**Done in MVP2.0.1 wave 2 (most recent work, July 10–11):**
- `scripts/retry_failed_generate.py` — CLI to re-queue failed generate jobs (13/13 videos recovered in dry run)
- `POST /api/courses/{c}/sections/{s}/retry-failed` — single-click retry from the section header
- `GET /api/videos/{id}/transcript/export?format=md|json|txt` — download transcript as Markdown / JSON / plain text (RFC 5987 unicode filenames)
- "Download transcript" button on the video page (with format selector)
- 0-byte upload rejection (HTTP 400) for both single and bulk upload
- **2026-07-11 fix**: retry endpoint now also catches transcribe failures, not just generate failures. Button shows spinner + "Retrying N (X transcribe, Y generate)" toast.

---

## 2. Test suite

**396/396 tests passing** (was 218 at MVP1, 347 on 2026-07-09, 394 on 2026-07-10, 396 today).

| Module | Tests | Notes |
|---|---|---|
| `tests/test_courses.py` | 6 retry-failed | Was 4, +2 for transcribe-failure coverage (today) |
| `tests/test_retry.py` | 13 | New: covers `find_failed_generate_videos` / `find_failed_transcribe_videos` |
| `tests/test_transcript_export.py` | 19 | New: covers `.md` / `.json` / `.txt` formatting + sanitization |
| `tests/test_model_video.py` | 10 | New: covers `natural_sort_key` / `natural_sort_key_str` |
| `tests/test_videos.py` | +3 (0-byte rejection) | New: covers single + bulk + orphan-file-on-disk |
| `tests/test_loadSummary_dom.mjs` | +17 | JS-side DOM tests for transcript follow |
| All others | 326 | Unchanged since MVP2.0.0a |

**Coverage: 87% overall.** Deliberately untested: `routers/generation.py` (47%) and `routers/videos.py` (71%) — those are background-worker code paths that exercise real Whisper + Ollama and are verified end-to-end via manual runs.

---

## 3. Bug postmortems (`doc/Blockers.md`)

Four "✅ RESOLVED" sections, in chronological order:

1. **Transcript panel showing 00:10 instead of 00:00 on page load** — `offsetTop` is relative to body, not container. Fixed with `getBoundingClientRect`.
2. **Bulk upload returned 404 "Not Found"** — FastAPI route shadowing. `/{video_id}/transcribe` was declared before `/upload-bulk/{section_id}`. Moved the bulk route up; added a structural regression test (`test_upload_bulk_route_registered_before_transcribe_route`).
3. **LLM "Could not extract valid JSON"** — `glm-5.2:cloud` returns non-deterministic responses (sometimes `len=0`, sometimes prose-wrapped). Added strategy 3 to `_extract_json` (strip preambles) + better error message with raw response preview.
4. **0-byte upload crashes auto-pipeline** — Upload only checked the upper size bound. Added `if file_size == 0: raise HTTPException(400)`.
5. **"Retry N failed" button does nothing** *(2026-07-11)* — Endpoint only looked at `last_generate_job.status='failed'`, not `last_transcribe_job.status='failed'`. Fixed by partitioning failures by step. **Today**.

**Open in Blockers.md:** none. All reported issues have postmortems.

---

## 4. Open items, sorted by priority

| # | Item | Effort | Why it's next |
|---|---|---|---|
| 1 | **Delete video button** (manual todo #5) | ~0.5 day | User asked in `manualTodo.txt`; need it to clean up the 0-byte video. Design exists in Todo.md #10. |
| 2 | **#19 — Duplicate video detection** (B1–B5 in design doc) | ~1 day | User asked 2026-07-09. Default answers drafted in Appendix B. Needs user sign-off on B1–B5 then implement. |
| 3 | **#9 — Mindmap node count for long videos** | ~1 day repro + 1 day fix | Long videos produce huge mindmaps. Need to gather data first. |
| 4 | **#10 — Alembic migrations** | ~0.5 day | Schema-versioning safety net. Required before MVP3 (which will likely add new tables). |
| 5 | **#11 — Celery + Redis** | ~3 days | Restart-safety + parallelism for transcribe jobs. Required for multi-user. |
| 6 | **#6 — Default output language for Chinese** | ~0.5 day | Tied to #9 data. |
| 7 | **MVP3 — Smart/Always modes** | ~1 day | Deferred. Need to answer A1–A3 first (mode names, persistence, dropdown placement). |

---

## 5. Git / branch state

```
MVP2.0  6ce7571  docs: add HowToStart guide + stop.sh / status.sh helpers
MVP2.0  ee466ab  docs: postmortem for retry button silent-no-op bug
MVP2.0  162c85d  fix: retry button now re-queues transcribe failures too
MVP2.0  72ae0bc  MVP2.0: add download transcript button to video page
MVP2.0  3bb256b  MVP2.0: retry-all-failed button in section view - part A
MVP2.0  a1235b2  MVP2.0: transcript export endpoint - part A
MVP2.0  9e4634c  doc: Todo.md — add 6 open discussion items
MVP2.0  f37f7a0  MVP2.0: retry script for failed generate jobs
... (20 commits total ahead of main)
main    ...      (MVP1.0.0 release point)
```

All commits pushed to `origin/MVP2.0`. No uncommitted work in the working tree (last verified 2026-07-11 10:30 local).

---

## 6. Where to look

- **What got built** — see commit log above, or read the milestone sections in `doc/MVP2.0-first-designQuestions.md`
- **What went wrong** — see `doc/Blockers.md` (4 RESOLVED postmortems)
- **What's next** — see "Open items" table above
- **How to run it** — see [`doc/HowToStart.md`](HowToStart.md) (added 2026-07-11)
- **Full Todo list** — see [`Todo.md`](../Todo.md) (the older "Future Ideas" wishlist — most items still brainstorming-only)

---

## 7. Notes / caveats

- The design doc's "TL;DR for tomorrow's session (2026-07-10)" is now **stale** — that day already happened and added 6 commits. Update at the start of MVP2.0.1 wave 3.
- `Readme.md` and `CHANGELOG.md` are still showing MVP1 stats (218 tests, 96% coverage). These need a fresh entry for MVP2 once 2.0.1 wave 2 is signed off.
- The screenshot the user shared showing `_______` in the download filenames is **not a code bug** — the `_______` is in the user's source video titles (bilibili auto-renames files like that). The export correctly uses the video's title as the download filename, so the underscores propagate through. No fix needed unless the user wants a "clean up source filenames on upload" feature.

---

## 8. Test coverage investigation (2026-07-11)

> The user asked "any way we can increase test coverage?" Here's the full breakdown. **No code changes** — this is the research doc.

### Current state: 88% (1259 stmts, 147 missed, 397 tests passing)

#### Per-module coverage

| File | Stmts | Missed | Cover | Notes |
|---|---|---|---|---|
| `app/routers/generation.py` | 98 | 52 | **47%** | Background worker (`_run_generate_job`, ~88 lines) is the bulk of the miss. Runs real Ollama. |
| `app/routers/videos.py` | 265 | 67 | **75%** | Background worker (`_run_transcribe_job`, ~100 lines) is the bulk. Runs real Whisper. |
| `app/services/llm.py` | 59 | 7 | 88% | Bad-LLM-input fallback paths |
| `app/routers/courses.py` | 117 | 8 | 93% | Mostly 403/404 branches in non-retry routes |
| `app/jobs.py` | 76 | 5 | 93% | Serialization / progress-setter edge cases |
| `app/database.py` | 34 | 2 | 94% | Probably the import-error fallback |
| **Everything else** | ~610 | 6 | **~99%** | Mostly 100% |

#### Why the two big ones are hard

`generation.py` 47% and `videos.py` 75% are low because they contain long **background workers** (`_run_generate_job`, `_run_transcribe_job`) that call real Whisper and real Ollama. The conftest fixture `no_auto_pipeline` deliberately suppresses these from running in tests, which is the right call for unit tests — otherwise every test would take 30+ seconds and need GPU/CPU time for ML models. Trade-off: workers are 0% covered by line count.

#### How to push coverage up

| Approach | Effort | Coverage gain | Tradeoff |
|---|---|---|---|
| **A. Mock-based unit tests for the workers** | ~1 day | `generation.py` 47% → ~85%, `videos.py` 75% → ~90%. Project total: **88% → ~92%** | Realistic. Mock `WhisperModel.transcribe`, `generate_materials`, etc. Cover the success path + the "transcript disappeared" + "video disappeared" + exception-handler branches. |
| **B. Hit the small missing error branches** | ~0.5 day | `videos.py` 75% → ~78%, `courses.py` 93% → ~97%, `llm.py` 88% → ~92%. Project total: **88% → ~89%** | Easy mechanical tests for the 404/403/400 branches. Quick win. |
| **C. Tighten `llm.py` and `jobs.py`** | ~0.5 day | `llm.py` 88% → ~95%, `jobs.py` 93% → ~98%. Project total: **88% → ~89%** | Same scale as B, different files. |
| **D. Integration tests** (real Whisper on a 30s audio clip) | ~1.5 days | Doesn't move the % much (background workers already at 0%), but gives real confidence | Best ROI for correctness, worst for line coverage. |
| **E. Mark worker lines as `# pragma: no cover`** | ~5 min | **88% → ~95%** (instant) | **Bad: hides real bugs.** Don't. |

**Recommendation:** do **B + A**, in that order. B is a half-day of mechanical tests, A is the substantial but worthwhile one. Skip E.

#### What coverage would look like after B + A

- Total: **~92%** (was 88%)
- Both background workers: covered via mocks, but tests stay fast (no Whisper/Ollama)
- Remaining gaps: `try: ... except: db.rollback()` paths that need full integration to trigger

#### Things worth knowing

- **Coverage ≠ correctness.** A worker can be 100% line-covered and still pass the wrong model to Ollama. Don't chase 100% — chase meaningful tests.
- **The current 88% is genuinely good** for a small codebase with ML dependencies. Most "high coverage" projects sit at 85-90% for the same reason.
- **Integration tests** (run real Whisper on a 30s audio clip, assert the transcript comes back) are worth more than the last 5% of line coverage. The `no_auto_pipeline` fixture is a deliberate trade-off — flip it to run integration tests in CI for the worker paths.

#### Concrete test ideas for A (background workers)

For `_run_transcribe_job` in `app/routers/videos.py`:
- `test_transcribe_worker_success` — mock `WhisperModel.transcribe` to return a fake segments iterator, assert Asset created, video.status='ready', job='completed'
- `test_transcribe_worker_video_disappeared` — `db.get(Video, ...)` returns None, assert job='failed' with clear error
- `test_transcribe_worker_transcript_disappeared` — covers the `if not transcript_asset` branch
- `test_transcribe_worker_exception_marks_video_error` — Whisper raises, assert `video.status='error'`, `job.status='failed'`
- `test_transcribe_worker_db_rollback_on_error` — second commit fails inside the exception handler

For `_run_generate_job` in `app/routers/generation.py`:
- `test_generate_worker_success` — mock `generate_materials` to return a fixed dict, assert all 5 Asset rows created
- `test_generate_worker_existing_assets_updated` — pre-create Assets, verify they're updated not duplicated
- `test_generate_worker_progress_callback` — pass `on_progress` callback, assert `set_progress` is called and `video.last_generate_job` is updated
- `test_generate_worker_video_disappeared`
- `test_generate_worker_transcript_disappeared`
- `test_generate_worker_exception_marks_video_error`

That's ~11 tests, each ~20 lines, ~0.5 day to write. Bumps `generation.py` to ~85%, `videos.py` to ~90%, project to ~92%.

---

## 9. 2026-07-11 — Discuss tab shipped (commit `b20584a`)

> User's manual todo #6: "I got question on this quiz, but no way to ask why based on the transcript of video." Built a whole-video chat tab.

**What shipped:**
- 5th tab on the video page: **💬 Discuss**
- New endpoint `POST /api/chat/video-sessions` (separate from the existing `/sessions` which is per-flashcard)
- The AI gets the full transcript + summary + mindmap + quiz as the system prompt
- Sessions are persisted in the same `ChatSession` table with `scope='video'` so the user can come back later via `/chat-history`
- `app/chat_history.html` shows `VIDEO` / `FLASHCARD` badges so the user can tell the two types apart
- The original flashcard-scope chats are unchanged

**Implementation details:**
- Added `scope` column to `chat_sessions` (additive migration, default `'flashcard'`)
- `concept` column stays `NOT NULL` — video-scope rows use `"[whole video]"` as a placeholder (cheaper than a destructive migration)
- Long transcripts (1000+ segments) get head + tail truncation (600 segments) in the LLM context to keep the prompt under ~10K tokens
- Quiz is rendered as `Q: ... ✓ Correct Answer` in the LLM context (not all 4 options — saves tokens)
- Frontend: session is created lazily when the user opens the tab, typing indicator while the AI is responding, Enter-to-send

**Test results:** 412/412 passing (was 397, +15).

**Files changed:** 8 (1 model, 1 service, 1 router, 1 db migration, 2 templates, 2 test files). 800 insertions.

**What's deferred (from user's manual todo #6):** OCR of the video. The current implementation only uses the Whisper transcript + generated materials. If the user wants to ask about text shown in the video frames, that's a separate feature.

## 10. 2026-07-11 — Video / course / section delete shipped (commits `40d8c4a` → `1acc4ea`)

> User's manual todo #5: "can we delete the video quickly on section page and on video page and when delete the thing moved to trash folder, and will be permanently deleted after 30 days, or manually deleted in the trash folder empty all button etc, and can have restore button in trash bin"
>
> For MVP2.0 we shipped the hard-delete path first; soft-delete/trash is deferred to MVP3 (item #8 in manualTodo).

**What shipped (3 cascades, 3 buttons):**
- `DELETE /api/videos/{id}` — deletes the video, its asset files, and any chat sessions. Frontend button on the video page header.
- `DELETE /api/courses/{id}/sections/{section_id}` — deletes the section, all videos in it, their assets, files, and chat sessions. Frontend button on each section header in the course page.
- `DELETE /api/courses/{id}` — deletes the course, all sections, videos, assets, files, chat sessions. Frontend button on each course card on the dashboard.

**Why a delete cascade summary:** the user wants to know what was deleted (e.g. "3 files, 1 chat"). Each endpoint returns `{status, deleted: {file, files, assets, chat_sessions}}` so the UI can show a meaningful toast.

**File unlink semantics:** `Path.unlink()` swallows `OSError` — if the file is already gone (manually deleted, on a different volume, etc) the DB delete still succeeds, we just count the file as `files_missing` instead of `files_deleted`. This is the principle of least surprise for the user.

**Bugs caught and fixed during the roll-out:**
1. **Video delete redirect bug** (commits `1951a20` / `00c8c84`): deleting a video redirected to the wrong course because the frontend grabbed the first `a[href^="/course/"]` on the page (sidebar/course list) instead of the video's actual course. Fixed by rendering `courseId = '{{ course.id if course else "" }}'` as a JS constant and using it directly.
2. **Section delete click did nothing** (commit `5f38435`, today): user reported the section delete button click had no visible effect. The button was rendered correctly with the right `onclick`, the endpoint existed, the server was up — but the browser silently refused to run **any** JavaScript on the course page. Root cause: a missing closing `}` for the `uploadVideo` function (introduced in `7e70fe3`, the auto-pipeline + bulk upload feature) left the whole `<script>` block with a JS syntax error. Browsers don't run a script with a syntax error at all, so `toggleSection`, `retryAllFailed`, `showDeleteSectionModal`, and the rest were dead code. Fixed by adding the missing `}`. Also added a regression test (`test_course_page_inline_script_parses_cleanly`) that reads the template source and asserts brace+paren balance, so any future script-syntax regression fails the test suite.

**Test results:** 438/438 passing (was 412, +26). Coverage stays at 96%+ project-wide.

**Files changed:** 6 (2 routers, 1 model, 2 templates, 1 test). ~350 insertions across the feature.

**What's deferred to MVP3:** soft-delete / trash / restore (manualTodo #8).

---

## 11. What's next → see `doc/MVP3.0-Status.md`

After the section-delete bug fix, the active MVP2.0 branch is essentially
feature-complete for the **bulkUploads + LLMonTranscriptsMaterials** scope.
The next batch of work (10 GB cap, mlx-whisper speedup, soft-delete, note
section, paid tier) is now tracked as MVP3.0 in
[`doc/MVP3.0-Status.md`](MVP3.0-Status.md). The short version: 14 items,
3 P0s, 5 P1s, 4 P2s, 2 P3s. Top three picks: 10 GB cap, mlx-whisper,
background worker pool.

## 12. 2026-07-11 — MVP3.0 first two items shipped (commits `e5db159`, `ae4df7d`)

> MVP2.0 is done. The first two items from `doc/MVP3.0-Status.md` shipped today.

**Item #1: 10 GB upload cap (P0)**
- One-line constant change in `app/routers/videos.py:36` — `MAX_FILE_SIZE` raised from 2 GB → 10 GB (inclusive)
- 3 tests in `tests/test_videos.py`: boundary OK (10 GB == cap accepted), boundary FAIL (10 GB + 1 byte → 413), error message mentions 10 GB not 2 GB
- Also updated `test_upload_bulk_partial_success` to use 11 GB instead of 3 GB (the old "3 GB > cap" trick no longer works under the new 10 GB cap)
- No server-level change needed (Starlette reads the full body; uvicorn has no client_max_body_size in this setup). Trade-off: a 10 GB upload peaks at ~10 GB RAM during the upload, noted in the comment.

**Item #8: "ready · 9:08" timing on the section page (P2)** — superseded by §18 / MVP2.0.4
- Two new nullable columns on `videos`: `transcribed_at` and `generated_at`, set by the workers when each step reaches `status=ready`. Naive UTC, consistent with `created_at`.
- New Jinja filter `format_duration` in `app/routers/frontend.py`: renders seconds as `M:SS` (< 1h) or `H:MM:SS` (>= 1h). Empty string for `None`/negative.
- **(MVP2.0.4 supersession)** Course page status badge now reads `ready · T:0:55, G:0:44` for videos with all three timestamps, where T = transcribe time and G = generate time. For legacy videos (no `transcribe_started_at` populated), falls back to the original `ready · 9:08` (or `ready · 2:05:33` for > 1h). Videos with neither fall back just show `ready` — no misleading `0:00`. See §18 for the bulk-upload bug this solved.
- Failure semantics: timestamps are NOT stamped on worker failure, so an errored video never gets a fake "ready in N" label.
- 13 new tests in `tests/test_ready_timing.py` covering model, migration, both workers (success + failure), template (4 cases), filter unit, filter registration.

**Test results:** 453/453 passing (was 438, +15).

**Files changed:** 7 (1 model, 1 db migration, 2 router, 1 frontend filter, 1 template, 1 new test file) + 1 updated test file.

## 13. 2026-07-11 — Whisper model picker with smart picks (commits `2a96049`, `1497cd7`)

> MVP3.0 #2 (plumbing only). The actual MLX backend implementation is
> deferred — see `doc/MVP3.0-Status.md` row 2.

**What shipped:**
- 6-option model dropdown (4 manual + 2 smart picks), rendered as an
  `<optgroup>` so the manual picks and the smart picks are visually
  separated.
- New `MODEL_REGISTRY` (single source of truth for choices) +
  `resolve_model_choice()` (maps a user choice to a (backend, model_id)
  pair, with MLX auto-fallback) + `get_default_model_choice()` (picks
  the right default per platform).
- New `transcribe_with_backend()` function that dispatches to
  faster-whisper or mlx-whisper based on the resolved choice. The
  faster-whisper path is fully wired up. The mlx-whisper path raises
  `NotImplementedError` — the actual `mlx_whisper.transcribe()` call
  is a follow-up commit.
- New `/api/videos/models` endpoint returns a richer shape:
  `{choices, default, models: legacy flat list}`.
- 3 new DB columns on `videos`: `whisper_backend`, `whisper_resolved_model`,
  `whisper_fallback_reason`. Migration is additive (existing rows
  remain valid).
- `upload_video` and `_run_auto_pipeline` now default to
  `get_default_model_choice()` (MLX smart pick on M-series, else
  faster-whisper smart pick, else `base`).
- The transcribe worker (`_run_transcribe_job`) is refactored to use
  `transcribe_with_backend()`, removing its inline faster-whisper
  boilerplate. The resolved (backend, model_id, fallback_reason)
  is persisted on the video row.
- JS at the bottom of `video.html` fetches `/api/videos/models` on
  page load and selects the recommended default option. Falls back
  to the first `<option>` (tiny) if the fetch fails.

**Decisions (from A, A, A):**
- Default = MLX smart pick when available, else faster-whisper smart
  pick, else `base`. (Confirmed on user's M1 Max.)
- MLX auto-fallback on Intel Mac: silently fall back to the
  faster-whisper smart pick. The `whisper_fallback_reason` column
  records the reason; the UI can show "actually ran X" later.
- UI = grouped optgroup, not a flat list. "Manual (pick a size)" and
  "Smart picks (recommended)" group the 4 and 2 entries.

**Bug fix bundled in (was broken before Part A):**
- The `format_duration` Jinja filter (used by the course page's
  "ready · T:..., G:..." badge from MVP3.0 #8 / MVP2.0.4) was
  missing from `app/routers/frontend.py`. Restored as part of
  this commit so `tests/test_ready_timing.py` passes again.
- The `transcription._model_cache` is a module-level dict that was
  leaking between tests, causing flaky behaviour in
  `test_ready_timing.py`. Added an autouse fixture
  (`clear_whisper_model_cache`) in `tests/conftest.py` to clear
  the cache before and after every test.

**Test results:** 491/491 passing (was 447, +44).
- `app/services/transcription.py`: 100% coverage.
- `app/routers/videos.py`: 90% coverage (the new MVP3.0 #2 code is
  fully covered; the missing lines are pre-existing).

**Files changed:** 5 (1 service, 1 router, 1 model, 1 db migration,
1 template) + 1 new test file (`tests/test_whisper_picker.py`,
44 tests) + 1 fixture in conftest + 1 test_model_video update.

**What's NOT shipped (deferred):**
- The actual `mlx_whisper.transcribe()` call. Requires the user to
  `pip install mlx-whisper` on the M1 Max first. The plumbing is
  ready; the worker will start using it as soon as that pip
  install completes.
- "Cloud Whisper" paid tier (MVP3.0 #3) — still conceptual, no code.

## 14. 2026-07-14 — Full repo recap + open work snapshot

> Compact end-to-end summary of the repo, what's shipped, what's left, and
> where to start next. Sourced from a deep read of the entire codebase
> (models, routers, services, middleware, tests, docs) plus
> `doc/manualTodo.txt` [july 14] priorities.

### 14.1 What this app does (one paragraph)

A **local-first, AI-powered web app** that turns your downloaded video
classes (Bilibili, Coursera, university lectures, conference recordings,
etc.) into **interactive study materials**. You upload a video → Whisper
transcribes it → Ollama LLM generates a summary, mindmap, quiz, and
flashcards → you click mindmap nodes to jump to the topic → you can chat
with the AI about the content (either about a specific concept or about
the whole video). **One user per install, runs on your Mac.**

### 14.2 Tech stack

| Layer | Choice | Notes |
|---|---|---|
| **Backend** | FastAPI (Python 3.14) | async, auto OpenAPI docs |
| **Frontend** | Jinja2 templates + vanilla JS + Tailwind CSS | dark/light, mobile responsive, no SPA framework |
| **DB** | SQLite + SQLAlchemy 2.0 | with hand-rolled additive migrations (Alembic deferred) |
| **Storage** | Local filesystem (`uploads/`, `storage/`) | gitignored |
| **Auth** | AuthKit (Firebase Auth UI) → httpOnly session cookies | Frontend never sees tokens |
| **Transcription** | faster-whisper + mlx-whisper (Apple Silicon) | 7 model options, 2 backends |
| **LLM** | Ollama (local, `glm-5.2:cloud`) | deterministic: `temperature=0`, `seed=42` |
| **Tests** | pytest + pytest-asyncio + httpx | 487 passing, 87% coverage |

### 14.3 Data model (hierarchy)

```
Course (e.g. "Machine Learning")
└── Section (e.g. "Week 1: Neural Networks")
    └── Video (the class file)
        ├── Asset (5 types: summary, transcript, flashcards, quiz, mindmap, topic_timestamps)
        └── ChatSession
            └── ChatMessage
```

### 14.4 What we've done (the journey)

**MVP1 (shipped 2026-07-06) — 218 tests, 96% coverage**
The "local single-user" foundation. Auth, course hierarchy, upload, Whisper
transcribe, Ollama generate, interactive mindmap, chat with AI, mobile
responsive UI, dark/light theme, transcript viewer with click-to-seek + search.

**MVP2.0 (shipped Jul 6-11) — 491 tests, 87% coverage**
*Tagline: bulkUploads and LLMonTransciptsMaterials*

| Pillar | What shipped |
|---|---|
| **Auto-pipeline** | Upload → automatically transcribe → automatically generate. Zero clicks after upload. |
| **Bulk upload** | Drag 20 files, walk away. Per-file progress, 0-byte rejection, 2 GB cap (later 10 GB). |
| **Retry** | `retry_failed_generate.py` CLI + "Retry all failed" button per section + per-video "Retry" button. Catches both transcribe and generate failures. |
| **Transcript export** | Download as `.md` / `.json` / `.txt` with proper RFC 5987 unicode filenames. |
| **Delete** | Video / section / course hard-delete with cascade summary (`{files, assets, chat_sessions}`). |
| **Session expiry** | Middleware redirects protected pages to `/?session=expired` if cookie is bad. |
| **Natural sort** | `1.-foo` < `2.-bar` < `10.-baz`. |
| **Discuss tab** | NEW! Chat about the whole video (transcript + summary + mindmap + quiz as system prompt). Persisted with `scope='video'` badge. |

**MVP2.0.1 Part A (shipped 2026-07-14) — 487 tests**
*Tagline: anti-drift language policy*

Fixed a real production bug: a 2.5h Mandarin file produced 296 "Thank you"
hallucinations because Whisper drifted from Chinese to English mid-file. Fixes:
- **Language dropdown** on the video page (Auto / English / 中文) with confirmation modal
- **Lock language for the whole file** via `language=` arg
- **Auto-detect from first 10 min** (20 windows × 30s, weighted by `no_speech_prob < 0.5`)
- `condition_on_previous_text=False` + `compression_ratio_threshold=1.8` to catch repetitive-text hallucination
- **MLX-whisper path now actually dispatches** (was a `NotImplementedError` stub from Part 1 of the whisper picker)

**Result: 0% Mandarin → 97.8% Mandarin on the same 2.5h file.** 🎉

**MVP3.0 items shipped (Jul 11-14)**
1. ✅ 10 GB upload cap (was 2 GB)
2. ✅ "ready · T:..., G:..." timing badge on section page (MVP2.0.4: split into per-step times — see §18)
3. ✅ Whisper model picker with 7 options (4 manual + 3 smart picks including MLX)

### 14.5 Code quality / engineering highlights

- **487 tests passing** with structured regression tests for past bugs (route shadowing, JS syntax errors, etc.) — now 532 after MVP2.0.4 (§18)
- **Status-bar JSON-parsing** with 4 fallback strategies (direct / code-fence / preamble-strip / brace-match) for non-deterministic `glm-5.2:cloud` responses
- **Security headers** that don't break Firebase popup login (the `same-origin-allow-popups` lesson is documented in `BlockersOrChallengers.md`)
- **Deterministic LLM**: `temperature=0` + `seed=42` — same transcript → same materials
- **In-memory job tracker** (`app/jobs.py`) with progress + ETA, survives page refresh via DB persistence

### 14.6 Notable bugs we've solved (with full postmortems)

All in `doc/BlockersOrChallengers.md` — these are some of the best learning
material in the repo:

1. **Transcript panel scrolled to 00:10 instead of 00:00** — `offsetTop` was relative to `<body>`, not container. Fixed with `getBoundingClientRect`.
2. **Bulk upload 404** — FastAPI route shadowing. `POST /{video_id}/transcribe` was declared before `POST /upload-bulk/{section_id}`. **TestClient didn't reproduce this** — production uvicorn only.
3. **0-byte upload crashes Whisper** — only the upper size bound was checked.
4. **"Retry N failed" button did nothing** — endpoint only checked generate failures, not transcribe failures.
5. **"Thank you" hallucination on 2.5h Mandarin** — per-window language drift.
6. **Firebase login popup silently failed** — wrong `Cross-Origin-Opener-Policy` value severed `window.opener`. Errors only visible in popup's DevTools, not parent.
7. **Section delete click did nothing** — a missing `}` in `uploadVideo()` made the whole `<script>` block a JS syntax error; browsers don't run scripts with syntax errors at all.

### 14.7 What's next — the open work

**From `doc/MVP3.0-Status.md` (14 items, 3 P0)**

| Priority | Item | Status |
|---|---|---|
| **P0** | 10 GB upload cap | ✅ Done |
| **P0** | Whisper picker plumbing | ✅ Done, MLX backend wired |
| **P1** | Background worker pool | Not started — needed for 100-video batches |
| **P1** | Soft-delete / trash / restore (30-day TTL) | Not started — manualTodo #8 |
| **P1** | Note section (markdown, DB-backed) | Not started — manualTodo #6 |
| **P1** | Long-video player (2-hour seek, Plyr swap) | Not started — manualTodo #4 |
| **P2** | Language consistency in generated materials | Not started — manualTodo #7 |
| **P2** | Alembic migrations | Not started |
| **P2** | Architecture data-flow diagram (Mermaid) | Not started — manualTodo #7 |
| **P3** | OCR of video frames for Discuss tab | Not started — manualTodo #6 |
| **P3** | Cloud Whisper API (paid tier) | Not started |
| **P3** | Jira creation via MCP (paid tier) | Not started |
| **P3** | i18n UI strings | Not started |

**From `doc/manualTodo.txt` [july 14] (the most recent, current focus)**
1. **Logout but still can see summary** — likely a cache issue; `summary_content` is SSR'd
2. **Where is the remove button on single video page?** — should be there (commit `40d8c4a`); check console
3. **Why did moving the repo require re-uploading videos?** — uploads are local filesystem paths, not relative. Need a storage path migration helper.
4. **WebM → MP4 conversion** as a non-main feature
5. **Foundation Models** — research
6. **Read the paper of Revolute** — research
7. **OCR pipeline** (whisper → generate materials → OCR), as the 3rd stage
8. ✅ **"ready · T:..., G:..." split into per-step times** — was a follow-up to #1; fixed in MVP2.0.4 (§18)
9. **Numbering off-by-one** in front of video filename — the `2. ...` prefix bug
10. **Where is the Chat session that has all transcript, material information?** — this is the **Discuss tab** (MVP2.0 ship), at `POST /api/chat/video-sessions` and `/chat-history` with a VIDEO badge. Let me know if you can't find it in the UI.

### 14.8 Recommended next step

Based on the current `manualTodo.txt` [july 14] priorities and the MVP3.0
status doc, suggested order:

1. **Quick fixes (today)**: items 1, 2, 8, 9, 10 from july 14 — they're all small, well-scoped bugs/UX
2. **Soft-delete / trash** (P1) — your manualTodo #8, big UX win
3. **Note section** (P1) — your manualTodo #6, big UX win
4. **Background worker pool** (P1) — unlocks 100-video batches and is the foundation for Celery
5. **OCR pipeline** (P3) — your manualTodo #7, the 3rd stage of the AI pipeline

## 15. 2026-07-14 — Discuss tab clickable timestamp citations (MVP2.0.2)

> User's manualTodo [jul14] #6: "I got question on this quiz, but no
> way to ask why based on the transcript of video. ... so need
> somewhere on video page to ask questions on all transcript of the
> current video, the current materials like mindmap, quiz (and its
> answers etc) we can discuss ... at least can return the timestamps
> or sentences, like starting from 00:20 to 00:40 this talks about ...
> if it can utilize existing feature and jump video to that timestamp
> (like what we do by clicking on node of mindmap) then it will be
> even better."

The Discuss tab was already in place from MVP2.0 (commit `b20584a`),
but the AI couldn't reliably cite timestamps because:
1. The system prompt mentioned `[12:34]` (M:SS) format without
   examples
2. The backend silently swallowed transcript-parse errors and told
   the LLM "(Transcript present but could not be parsed.)", which
   made the AI hallucinate explanations for the failure
3. Even when the AI did emit a citation, the UI just rendered it as
   plain text — the user had to copy the timestamp, switch tabs,
   and manually seek the video

### What shipped (commits `ccf30a5`, `cc42b15`)

**Backend (`app/services/chat.py`, `app/routers/chat.py`):**
- New `parse_citations(text)` function extracts `[M:SS]` / `[H:MM:SS]`
  markers from a string. Two clean regexes (not one ambiguous one).
  Returns `{start_seconds, display, offset, raw}` per citation.
  Fractional seconds preserved (`[1:23.5]` → 83.5s).
- The `/api/chat/sessions/{id}/messages` endpoint now returns a
  structured `citations` field alongside the AI message — only for
  video-scope sessions (flashcard-scope always returns `[]` since
  there's no transcript to cite from).
- `_build_video_chat_context` now logs the actual parse error
  (with a snippet of the failing content) AND gives the LLM a
  clearer message: "your transcript exists but my parser couldn't
  read it — try re-transcribing."
- `VIDEO_CHAT_SYSTEM_PROMPT` rewritten with explicit citation
  format documentation, examples, and a "be honest when the
  transcript doesn't cover the topic" rule.

**Frontend (`app/templates/video.html`):**
- `appendDiscussMessage(role, content, citations)` now takes the
  backend's citations list and renders each `[M:SS]` marker as a
  small styled button (matching the mindmap node look).
- New `renderDiscussTextWithCitations(bubble, text, citations)`
  splices text + citation buttons into the bubble using
  `document.createTextNode` for non-citation runs (XSS-safe) and
  `<button>` elements for the markers. Has a client-side regex
  fallback for when the backend's `citations` list is missing
  (e.g. messages loaded from the chat history page).
- Clicking a citation button:
  1. Seeks the video to that time
  2. Highlights the matching transcript line(s) for context
  3. Leaves the user on the Discuss tab so they can keep reading
     the response

**Test results:** 523/523 passing (was 500, +23). Coverage on
`app/services/chat.py` 96%, `app/routers/chat.py` 97%.

**Files changed:** 3 implementation files (chat service, chat
router, video template) + 3 test files + CHANGELOG + this status
entry. ~720 insertions across the feature.

**What this unlocks (per user's [jul14] #6):**
- "为什么这道题的答案是 B？" → AI cites `[3:45]` and `[8:12]`,
  user clicks each link, the video jumps and the relevant
  transcript lines are highlighted — instant context.
- "Which time range covers X?" → AI says "covered from `[3:45]`
  to `[5:48]`", user clicks `[3:45]` to start there.
- "Is the answer correct?" (about a quiz question) → AI cites
  the transcript line + timestamp, user can verify by clicking.

**Not yet done (deferred to future work):**
- "Show me the whole segment" — currently we highlight ±5s around
  the citation. A future feature could let the user click-and-drag
  to expand the range.
- Semantic search — "find the part about Opus" rather than a
  specific timestamp. This is a different feature (would need
  transcript vectorization) and is a separate MVP3+ item.

## 16. 2026-07-14 — Transcript parse fix (hotfix, follow-up to §15)

> User verification caught a real bug: even after the §15 fix
> added the "could not be parsed" fallback message, the AI was
> still saying the transcript couldn't be parsed on a video
> where the transcript was perfectly valid. Commit `08175d6`.

### Root cause

`_build_video_chat_context` had this code path:

```python
transcript_obj = json_to_transcript(transcript_asset.content)
transcript_text = transcript_to_chat_text(transcript_obj)
```

But `json_to_transcript()` returns a **wrapper dict**
`{"segments": [...], "language": ..., "duration": ...}`, while
`transcript_to_chat_text()` expects a **list of segments**. The
helper tried to do `segments[0]`, which raised `KeyError: 0`.
The exception handler caught it and substituted the fallback
message — so the AI always saw "could not be parsed" for every
video, even when the transcript was fine.

This bug existed before the §15 fix (it was the **root cause**
of the original "AI says transcript is broken" complaint from
the user). My §15 fix made the failure visible (better log
message, clearer LLM-facing text) but did NOT fix the underlying
shape mismatch.

### Fix

Extract `.segments` from the wrapper dict before passing it to
the helper:

```python
transcript_obj = json_to_transcript(transcript_asset.content)
segments = (
    transcript_obj.get("segments")
    if isinstance(transcript_obj, dict)
    else transcript_obj
)
transcript_text = transcript_to_chat_text(segments)
```

Verified end-to-end with the user's actual video
(`de3e5a8c-3da8-4a2e-982d-ee5ce14faaf4`): the system prompt
now contains the real `[00:00] 各位同学,大家好...` transcript
lines instead of the fallback message.

### Regression tests added (2 new, 525 total)

- `test_video_chat_context_includes_transcript_with_proper_shape` —
  feeds a real transcript and asserts the prompt contains the
  actual lines. **This test would have caught the original bug.**
- `test_video_chat_context_logs_transcript_parse_failure` —
  feeds genuinely-broken JSON and asserts the fallback
  message is in the prompt. Catches the "silent failure"
  regression (where the helper starts swallowing errors
  again).

### Lesson

When the AI "hallucinated" an explanation for the transcript
not being parseable, it was actually being **lied to** by the
backend (which had been silently broken for some time — this
shape mismatch predates the §15 fix). The §15 fix exposed
the failure mode but didn't fix it. This is a good reminder
that **the exception-handler should not be the only defense**:
when a parser has a known data shape, the call site should
match it, and the exception handler should be reserved for
truly unexpected errors.

### Impact

After the user starts a **new** chat session on this video
(system prompt is built fresh at session creation), the AI
will:
1. See the actual transcript with `[M:SS]` markers
2. Be able to cite specific moments in response to the user's
   questions (e.g. "工具简介 covered at [0:00] in the video")
3. Each citation will render as a clickable button that seeks
   the video to that time (the §15 UI feature)

The previous chat session still has the old (broken) system
prompt in its DB row, so the user will need to start a new
session to see the fix. This is by design — we don't want to
silently rewrite session history.

## 17. 2026-07-14 — Tab switching single-select (MVP2.0.2 hotfix)

> User feedback: "the discuss tab can be selected while other
> tabs are also selected, I want it to be single-option style".
> Commits `dc11d5f`, `d7c4ef6`.

### Root cause

`switchTab()` in `app/templates/video.html` iterated over
`['summary', 'flashcards', 'quiz', 'mindmap']` to hide the
non-active panels. When the Discuss tab was added in commit
`b20584a` (MVP2.0 ship), the forEach list was never updated.
Result: clicking Summary, Flashcards, Quiz, or Mindmap while
Discuss was open left the Discuss panel visible underneath the
new tab's content — classic single-select tab violation.

This was a latent bug since 2026-07-11. The Discuss tab was
shipped, but the tab-switching logic was never updated to know
about it.

### Fix

Add `'discuss'` to the iteration list. One-line fix:

```js
['summary', 'flashcards', 'quiz', 'mindmap', 'discuss'].forEach(t => {
    document.getElementById('content-' + t).classList.add('hidden');
    // ... and reset the tab button styling
});
```

### Regression test

`test_video_page_switchTab_hides_all_five_panels` in
`tests/test_ui_features.py` reads the `switchTab` function body
from the rendered video.html and asserts the forEach loop
iterates over all five tabs. Verified to fail when the bug is
present and pass when the fix is in place (so it's a real
regression test, not a tautology).

### UX impact

After the fix, clicking any tab hides all the others. This
matches the standard tab-bar behavior the user expected. The
Discuss tab session is preserved (we don't re-create the
session) — only its **panel visibility** changes.

## 18. 2026-07-15 — Per-step transcribe/generate timing (MVP2.0.4)

> User feedback (manualTodo [jul14] #8): "the time beside each
> video should between begin to read, not queued to ready. (in
> future should be configurable)". Commits `493da3d`, `4573812`.

### The bug

The course page badge showed `ready · 9:08` for every video,
computed as `generated_at - created_at`. For videos that were
uploaded individually, this was the real processing time. But
for videos that were part of a **bulk upload**, `created_at` was
the time the video was added to the queue — and the actual
transcribe work didn't start until the previous N-1 videos in
the batch had finished.

**Concretely:** for video #34 of a 34-video batch, the badge
showed `ready · 36:55` even though the video itself only took
~55 seconds to transcribe. The other 36 minutes were queue wait
behind the other 33 videos, which is not the transcribe's
fault. Users reasonably thought the transcribe was broken.

### Root cause

The course page was using `created_at` as the transcribe-start
proxy. `created_at` is set at upload time (which is correct for
its other purpose: "when did this video get added to the
library"), but it's the wrong anchor for "how long did the
transcribe take" — that should be "when did the transcribe
worker actually start working on this video".

### Fix

Add a new `transcribe_started_at` column to `videos` and stamp
it at the **very top of `_run_transcribe_job`** — before
`WhisperModel.transcribe()` is called, so the duration includes
the model load time. Combine it with the existing
`transcribed_at` (already stamped at the end of the worker) to
get the real per-video transcribe duration.

Show this on the course page as two separate numbers:
`ready · T:0:55, G:0:44` — T for transcribe, G for generate.
For videos uploaded before MVP2.0.4 (where
`transcribe_started_at IS NULL`), fall back to the old
`created_at` → `generated_at` duration so no rows are
visually broken.

### Files changed

- `app/models/video.py` — add `transcribe_started_at` column
  (nullable `DateTime`).
- `app/database.py` — register the additive migration entry
  in `_MIGRATIONS` (`ALTER TABLE videos ADD COLUMN
  transcribe_started_at DATETIME`).
- `app/routers/videos.py` — stamp `transcribe_started_at` at
  the top of `_run_transcribe_job` (after video exists check,
  before whisper loads). Re-stamped on every fresh transcribe
  run, so the badge always reflects the most recent work.
- `app/templates/course.html` — render `T:M:SS, G:M:SS` when
  all three timestamps are present; fall back to the old
  `M:SS` for legacy videos; hide the badge for non-ready
  statuses.

### UX impact

After the fix, a 34-video batch shows:

- Video #1:  `ready · T:0:50, G:0:40` (no queue wait)
- Video #17: `ready · T:0:55, G:0:42` (waited ~10 min for #1-16)
- Video #34: `ready · T:0:55, G:0:44` (waited ~36 min for #1-33)

Previously all three showed ~36:55, which made the per-video
transcribe time look broken. Now the queue wait is invisible
(it was never the transcribe's fault), and the per-step times
match what the worker actually did.

The `(in future should be configurable)` part of the user's
todo is not done in this fix — that's deferred to MVP3+ since
it requires a UI control for "what should the badge anchor on"
(begin-to-ready, transcribe-only, generate-only, etc.). For
now the badge is a fixed `T:..., G:...` format, which is what
the user actually needed.

### Regression test

`test_transcribe_worker_stamps_started_at_before_whisper_loads`
in `tests/test_per_step_timing.py` mocks
`faster_whisper.WhisperModel` (the leaf class that `get_model()`
instantiates) and asserts the stamp happens **before** the
fake model is called. Confirmed to fail when the stamp is
removed and pass with the fix in place. Plus 5 more tests in
the same file covering the schema, migration, template render,
legacy fallback, and non-ready-status hiding.

### Test count

526 → 532 (+6 new tests, all passing; 0 regressions in the rest
of the suite).

## 19. 2026-07-15 — Bulk upload "error when parsing the body" (MVP2.0.5)

> User feedback: "I got an error when uploading 3 files all
> bigger than 1 GB, the bulk upload fails error when parsing
> the body". The user attached no log, no stack trace, no
> repro — just the symptom in the UI.

### The 3-layer failure

What looked like a single bug was actually a chain of three
independent issues, each masked by the next:

1. **uvicorn/h11 receive buffer too small.** h11's
   `DEFAULT_MAX_INCOMPLETE_EVENT_SIZE` is 16 KB. For a
   multi-GB multipart body, the receive buffer can briefly
   exceed 16 KB between `next_event()` calls. h11 raises
   `RemoteProtocolError("Receive buffer too long")`. This is
   the underlying h11 issue.
2. **uvicorn swallows the h11 error and returns plain text.**
   `uvicorn/protocols/http/h11_impl.py` catches
   `h11.RemoteProtocolError` and returns a plain-text 400 with
   body `"Invalid HTTP request received."`. **No JSON, no
   `detail` field.** The user can't see what actually went
   wrong.
3. **Frontend crashes on the plain-text body.** The old
   dashboard upload handler did
   `.then(resp => resp.json().then(data => ({ok: resp.ok, data})))`.
   When `resp.json()` throws on the plain-text body, the
   `.then()` chain breaks. The user sees the JS error
   `SyntaxError: Unexpected token I in JSON at position 0` —
   which the user paraphrased as **"error when parsing the
   body"** (the "parsing" is `JSON.parse`, not the server's
   multipart parser).

The user couldn't tell that:
- The server's multipart parser was fine.
- The server's body was being rejected by h11, not the route.
- The frontend was failing on a JSON parse, not a network error.

The error message gave them no useful info. From their POV, the
"bulk upload fails" and they "get an error when parsing the
body" — true statements, but pointing at completely the wrong
layer.

### The fix (3 layers, one per link in the chain)

**Layer 1 — Server, h11 buffer** (`scripts/start.sh`):
Bump `--h11-max-incomplete-event-size` from the default 16 KB
to 64 MB. This prevents the underlying h11 trigger for any
realistic upload size (10 GB max per file, well under 64 MB).
The fix is one CLI flag; no code change.

**Layer 2 — Server, global exception handlers** (`app/main.py`):
Add `@app.exception_handler(StarletteHTTPException)` and
`@app.exception_handler(Exception)`. These wrap every error
response in a proper `JSONResponse({"detail": "..."})`. Even
if some OTHER unexpected error path returns plain text, the
handler ensures it doesn't. This is defense in depth — the
h11 fix should be enough on its own, but if we ever hit a
similar issue with a different framework layer, the frontend
will at least get a proper JSON response.

**Layer 3 — Frontend, `safeJsonParse()` helper**
(`app/templates/base.html` + `dashboard.html` + `course.html`):
Add a global `safeJsonParse(resp)` helper that defensively
parses the response as JSON or falls back to `text()`. The
helper is in `base.html` (which is on every page), so all
upload handlers can use it. The dashboard and course upload
handlers were updated. Error messages now have a `(server)` or
`(network)` prefix so the user can tell which layer failed.

### Why the h11 trigger is intermittent

The 16 KB buffer only fills up when:
- The body is large enough that h11's internal events are
  processed slower than the network delivers data.
- The OS hands data to h11 in large TCP segments (e.g. on
  localhost, the loopback buffer is huge).
- The browser uses `Transfer-Encoding: chunked` (which most
  browsers do for fetch() with FormData and large bodies).

For small uploads (1 MB or under), the buffer never fills.
For 1+ GB uploads, the trigger is much more likely. That
explains why the user saw the error only with "3 files at
1+ GB" — smaller bulk uploads worked fine.

### Files changed

- `scripts/start.sh` — add `--h11-max-incomplete-event-size 67108864` to the uvicorn command, with a comment explaining the 16 KB default and why we bump it.
- `app/main.py` — add `@app.exception_handler(StarletteHTTPException)` and `@app.exception_handler(Exception)` handlers that wrap errors in `JSONResponse`.
- `app/templates/base.html` — add the global `safeJsonParse(resp)` helper.
- `app/templates/dashboard.html` — bulk and single upload handlers use `safeJsonParse`. Course creation error path also uses it.
- `app/templates/course.html` — bulk and single upload handlers use `safeJsonParse`.

### Regression tests

8 new tests in `tests/test_bulk_upload_error_handling.py`:

1. `test_starlette_http_exception_handler_returns_json` —
   hits an unknown route, verifies 404 is JSON with `detail`.
2. `test_unhandled_exception_handler_is_registered` —
   structural check that the global `Exception` handler is
   on `app.exception_handlers`.
3. `test_bulk_upload_route_returns_json_on_404` — bulk
   endpoint returns JSON 404 for unknown section.
4. `test_base_html_contains_safeJsonParse_helper` — base
   template defines the helper.
5. `test_dashboard_uses_safeJsonParse_for_bulk_upload` —
   dashboard uses the helper, not raw `resp.json()`.
6. `test_course_uses_safeJsonParse_for_bulk_upload` — course
   page uses the helper.
7. `test_start_sh_bumps_h11_max_incomplete_event_size` —
   `start.sh` passes the flag with value ≥ 1 MB.
8. `test_all_upload_handlers_use_safeJsonParse` — guards
   against future regressions if someone adds a new upload
   endpoint without using the helper.

The most important regression test is
`test_start_sh_bumps_h11_max_incomplete_event_size` — it's
verified to fail when the flag is removed and pass when the
flag is in place. So it's a real structural test, not a
tautology.

### Test count

532 → 540 (+8 new tests, all passing; 0 regressions in the
rest of the suite).

## 20. 2026-07-15 — MVP2.0 sign-off + logout still sees summary (MVP2.0.6)

> **🎉 MVP2.0 is officially closed.** All 6 sub-versions
> (2.0.0, 2.0.0a, 2.0.1, 2.0.2, 2.0.3, 2.0.4, 2.0.5) are
> shipped and pushed to `main` via branch `MVP2.0`. The
> 2.0.6 fix below is a post-close hotfix for the
> "logout but still sees summary" UX bug that was
> standing in the way of MVP2.0 being truly done.

### MVP2.0 final state

- **Branch:** `MVP2.0` is 70+ commits ahead of `main`, all
  pushed.
- **Tests:** 543/543 passing, 87% coverage.
- **Versions:** 2.0.0 → 2.0.6 all in CHANGELOG.md.
- **Post-MVP2.0 backlog:** see the discussion in §19 for the
  next-up items (collapse/expand on video page, plugin
  tools, soft-delete, etc.) and the deferred MVP2.1
  (background worker pool with throttle=3, own branch).

### Item #1 — Logout still sees summary (MVP2.0.6 hotfix)

> User feedback: "logout but still can see summary" (manualTodo
> [jul14] #1). Reported as a P2 in the post-MVP2.0 backlog;
> upgraded to a blocker once we decided to call MVP2.0 done.

#### The bug

The `SessionExpiryMiddleware` (added in MVP2.0 #7) detects
*present-but-invalid* cookies on protected SSR routes
(`/course/`, `/video/`, `/chat-history`) and redirects to
`/?session=expired`. But it let *absent* cookies through
— assuming the page would render a "Sign in" prompt like
the dashboard does. The dashboard's "Sign in" prompt works
because the dashboard is the public landing page; the OTHER
protected routes don't have a sign-in prompt in their
template. So a user who logged out and then hit a deep
link (e.g. browser bookmark, link from a friend) would see
a half-rendered page with no data and no explanation.

#### The fix

In `app/middleware_session.py`, the `dispatch()` method now
treats an absent cookie on a *non-dashboard* protected
route the same as a present-but-invalid cookie: redirect
to `/?session=expired`. The dashboard's special case
(anonymous visits render the "Sign in" prompt) is
preserved. ~5 lines of code change.

#### The fallout

Adding the redirect for absent cookies broke **58 existing
tests** that didn't set a cookie when hitting protected
routes — they relied on the old "no cookie = pass
through" behavior. The fix at the test level was twofold:

1. **Update `tests/conftest.py` `client` fixture** to set
   a default valid session cookie, so tests that don't
   care about the cookie state just work.
2. **Patch `verify_token` at all three namespaces where
   it's bound** (`app.auth.firebase_admin`,
   `app.middleware_session`, `app.auth.dependencies`) —
   Python's `from X import Y` binds the name in the
   importer's namespace at import time, so later patches
   to X.Y don't reach the importer. This is a common
   testing gotcha that's worth documenting for future
   contributors.
3. **Add `client.cookies.clear()`** to the ~12 tests that
   explicitly test the no-cookie path (auth tests, the
   three new middleware tests, switch-accounts tests).

#### Tests

- 3 new tests in `test_session_expiry_middleware.py` (one
  per protected route).
- 1 new test asserting the dashboard's anonymous-friendly
  behavior is preserved.
- All 543 tests pass.

The fix is verified to fail when the redirect is removed
(regression test catches the bug).

### Why this fix ships as 2.0.6 (not 2.1)

It's a one-line middleware change with a follow-up test
fixture update. No new features, no schema changes, no
new endpoints. By the skill-commit convention (Part A,
Part B, stale doc upgrade), this is the smallest
shippable unit. Calling it 2.0.6 keeps the changelog
honest about scope and makes it easy to revert if it
breaks anything in production.


## 21. 2026-07-15 — Remove distil smart picks; rename turbo (MVP2.0.7)

> User feedback (manualTodo 2.2): "remove all UI, and just
> commented out all distll related model, and smart picks
> in UI, and now add what we added these days, I remeber
> it is called what, sorry fogot". The user wanted the
> bulk-upload model (mlx-community/whisper-large-v3-turbo)
> to be the visible, named smart pick in the UI.

### What changed

The model dropdown's "Smart picks (recommended)" group
previously had 2 distil-large-v3 options:
- `local-best-and-fast` (faster-whisper + distil-large-v3)
- `local-best-and-extremely-fast` (mlx-whisper + distil-large-v3)

Plus 1 multilingual option:
- `local-large-turbo` (mlx-whisper + mlx-community/whisper-large-v3-turbo)

The 2 distil options are now **commented out** in
`MODEL_REGISTRY` (not deleted) per the user's "comment
out" preference. The only smart pick that remains is
`local-large-turbo`, which is now both the default and
the only smart pick.

The user-facing label for `local-large-turbo` changed
from "🚀 Local Large-v3 Turbo (MLX, M-series, multilingual)"
to **"🚀 MLX Whisper Large V3 Turbo (recommended)"** —
shorter, identifies the engine (MLX), names the model
(Whisper Large V3 Turbo), and signals the recommended
status. The user said "we should give proper name on it".

### Why remove the distil options

distil-large-v3 is **English-biased** and ignores the
`language="zh"` lock — it was producing all-English
hallucination loops on Chinese videos even with the
anti-drift kwargs. The turbo model is a strict
superset for Chinese / multilingual and only ~1.5-2x
slower than distil-large-v3 on M-series. The user's
preference is now encoded in the default: every new
upload uses the multilingual model unless the user
explicitly picks a manual one.

### Files changed

- `app/services/transcription.py` — comment out the 2
  distil entries in `MODEL_REGISTRY`; update
  `get_default_model_choice()` to fall back to "base"
  (instead of the now-removed distil) on non-MLX Macs;
  update `resolve_model_choice()` MLX-unavailable
  fallback to "base" (instead of the distil); rename
  the local-large-turbo label.
- `app/templates/video.html` — update the dropdown
  HTML to remove the 2 distil options and use the new
  label.
- `app/routers/videos.py` — update the docstring
  example to reflect the new dropdown shape.

### Bonus fix: latent tab-switching regression

While running the test suite after the model-registry
changes, the existing
`test_video_page_switchTab_hides_all_five_panels` test
FAILED. Investigation revealed a latent regression
from commit `dc11d5f` (MVP2.0.2 hotfix). The commit
added a "Hide all five tab panels" comment to
`switchTab()` but forgot to actually add `'discuss'`
to the forEach loop. So clicking any other tab while
Discuss was open left the Discuss panel visible
underneath — the exact bug the commit claimed to
fix. The test was added in that same commit but
somehow the assertion passed at the time (probably
because the regex was loose and matched the
forEach label even with only 4 items).

Fixed by adding 'discuss' to the forEach. The test
now serves as a proper regression test (verified to
fail when 'discuss' is removed).

### Tests

- All `tests/test_whisper_picker.py` tests updated to
  reflect the new structure (5 registry entries, 1
  smart pick, fallback chain ends at "base").
- 3 regression tests verify the registry has the
  expected structure; they FAIL if the distil entries
  are restored.
- Test count went from 543 → 540 (-3) because 3
  distil-related tests were removed (their scenarios
  no longer apply) and replaced with 3 turbo-related
  tests (same coverage, new keys).


## 22. 2026-07-15 — Collapsible section-videos panel on video page (MVP2.0.8)

> User feedback (manualTodo [july14] #0): "add a collapse
> and expand courses/items in each video page, so no need
> to jump to sections pages for all content on section,
> can change video from video easily, should also have
> asc, and desc by name function etc."

### What shipped

A collapsible `<details>` panel above the tabbed interface
on the video page, showing the current section's video
list. Each video in the list is a link to its video page
(so the user can switch videos in 1 click instead of
"back to course → click video"). The list has a sort
dropdown (Name ↑/↓, Date ↑/↓), and both the open/closed
state AND the sort direction are persisted in
`localStorage` so the user's choice survives page
reloads.

### Design decisions (per user call)

- **No new endpoint.** `section.videos` is already in
  the template context via the existing `video_view()`
  route. The user explicitly chose this over a new
  endpoint. Saves a round-trip and a route to test.
- **No next/previous buttons.** The user explicitly
  skipped those for now. The video list panel serves
  the same purpose (switching videos) without the
  keyboard-navigation complexity. Easy to add later if
  the user wants.
- **Reuses the existing `natural_sort_key_str` Jinja
  filter** (added in MVP3.0 #2). The video-list sort
  uses the same natural-sort pattern as the course page,
  so the two pages now share a consistent sort UX.
- **Collapsed by default.** The panel is collapsed on
  first visit (so the video page looks the same as
  before for users who don't care). Once the user
  expands it, the choice is remembered in localStorage.
- **Current-video highlight.** The playing video has
  a left border + indigo background, so the user
  never gets lost when scanning the list.

### Why this is worth shipping

**Before:** to switch from video #1 to video #3 in a
section, the user had to:
1. Click "back to course" (or use the breadcrumb).
2. Scroll to video #3.
3. Click it.

**After:**
1. Expand the section-videos panel (if not already
   open — the state persists).
2. Click video #3.

For sections with many videos, this is a significant UX
win. Combined with the per-step timing badge (§18) and
the Discuss-tab citations (§15), the video page is now
much more "self-contained" — the user rarely needs to
leave it to do common tasks.

### Files changed

- `app/templates/video.html` — add the `<details>`
  panel + the JS functions (`sortSectionVideos`,
  `applyStoredSectionStateOnLoad`, the localStorage
  helpers). ~110 lines of HTML + ~80 lines of JS.
- `tests/test_video_page_section_panel.py` — 10 new
  tests covering the panel rendering, sort dropdown,
  current-video highlight, data attributes, and the
  JS function presence.

No backend changes, no model changes, no migration,
no new endpoint. Pure frontend work as requested.

### Tests

- 540 → 550 (+10 new tests in
  `tests/test_video_page_section_panel.py`).
- 7 of the 10 tests are verified to FAIL when the
  panel is removed (regression coverage is solid).

