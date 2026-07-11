# MVP2 Status — 2026-07-11

> **TL;DR**: MVP2.0 is **~70% shipped** on branch `MVP2.0` (20 commits ahead of `main`, all pushed, 396/396 tests passing, 87% coverage). MVP2.0.0 + 2.0.0a + 2.0.1 wave 1 are done. Wave 2 (delete button, duplicate detection) and MVP2.0.2 (i18n, mindmap tuning) are still open.
>
> For full milestone history, see [`doc/MVP2.0-first-designQuestions.md`](MVP2.0-first-designQuestions.md). For bug postmortems, see [`doc/Blockers.md`](Blockers.md).

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
