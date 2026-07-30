# Changelog

All notable changes to the Video Learning App are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-07-06 — MVP1 release

🎉 **First stable release.** MVP1 (local single-user foundation) is feature-complete, tested, and signed off. See [`doc/MVP1.0-successfullyFinished.md`](doc/MVP1.0-successfullyFinished.md) for the full scorecard.

**Stats:** 218 tests passing · 96% backend coverage · Python 3.14 · FastAPI · SQLite · Ollama

### ✨ Features

- **Auth flow** — AuthKit Firebase login + httpOnly session cookie exchange. Frontend never sees tokens.
- **Course hierarchy** — Course → Section → Video with sidebar navigation and real-time search.
- **Video upload** — Drag-and-drop or file-picker to any section, with per-section progress.
- **Whisper transcription** — 4 model sizes (tiny/base/small/medium), per-sentence timestamped output.
- **LLM learning materials** — Ollama with `temperature: 0` + `seed: 42` (deterministic). Generates summary, mindmap, flashcards, quiz, **and `topic_timestamps`** in a single call.
- **Clickable mindmap** — Markmap nodes navigate to the video timestamp, with a topic banner and transcript-line highlighting.
- **Mindmap controls** — Inline + fullscreen, with zoom (+/–), fit, drag/pan, scroll-zoom, and Ctrl+0 reset.
- **Ancestor-walking timestamp lookup** — When you click a leaf node the LLM didn't timestamp, the page walks up the tree to find the closest ancestor's timestamp.
- **Transcript viewer** — Click any timestamp to seek; live search with highlight + prev/next navigation.
- **Chat interface** — "💡 Teach me real-world usage" on any flashcard opens a per-topic AI chat.
- **Chat History page** — `/chat-history` lists all past chats, lets you continue or delete them.
- **Mobile-responsive** — Hamburger sidebar, stacked layouts on small screens, no horizontal overflow.
- **Dark / light themes** — Tailwind `class` strategy, toggle in sidebar footer.

### 🐛 Bug fixes

- **`fix(auth):` switching accounts in the same browser now works** (`754d614`) — force-sign-out on `/login` and gate the redirect behind an explicit user click to defeat the Firebase IndexedDB cache re-hydration race.
- **`fix(mindmap):` disable markmap's built-in d3-zoom so drag-to-pan works after zooming** (`0eb3878`) — markmap's d3-zoom was rewriting the inner `<g>`'s transform on every drag, blowing away the user's pan.
- **Dashboard upload zone was a stub** (`3ce42f0`) — now actually uploads via real `uploadToSection()` with drag-and-drop and a section picker.
- **Inline mindmap had no pan/zoom** (`f167fe0`) — attached `attachMindmapInteraction` to the inline view too; removed the 300ms safety-net re-fit that was clobbering user gestures.
- **Video page overflows on mobile** (`d2e2b0c`) — `<video>` with `w-full` forced 1280px width; fixed with `min-w-0` on flex containers.
- **Mobile sidebar toggle shows correct icon** (`b112f60`) — hamburger (☰) when closed, ✕ when open, mirroring actual state.
- **Sidebar search ReferenceError on highlight** (`c19dac0`) — `highlightMatch()` called `escapeHtml()` which was scoped to chat_history.html; moved the helper to `base.html`.
- **Mindmap ancestor timestamps** (`92a58f1`) — walk up the tree so leaf nodes are clickable too, not just level-1 branches.
- **3 mindmap UX issues** (`c5bfc1d`) — banner position, fullscreen auto-fit, post-fullscreen layout shift.
- **LLM determinism** (`9632b77`) — same transcript now produces the same mindmap every time (`temperature: 0` + `seed: 42`).
- **Markmap rendering with `Markmap.create()` directly** (`de7755b`) — autoloader was producing empty `foreignObject`s when the container was hidden.
- **Mindmap SVG sizing + content caching** (`646ca68`) — explicit pixel dimensions; tabs no longer re-fetch.
- **Center and scale mindmap via `getBBox()`** (`69a3b49`) — was showing a tiny blob in the top-left.
- **HTMX → JS fetch for course creation** (`64cff68`) — HTMX was sending form data; the API expects JSON.
- **`getIdToken()` from AuthKit's `user._raw`** (`6d18c26`) — public field was unreliable; the raw Firebase user object has the method.
- **AuthKit `baseUrl` for provider loading** (`c35e46b`) — providers weren't loading from the right URL.

### 📚 Docs

- [`doc/MVP1.0-successfullyFinished.md`](doc/MVP1.0-successfullyFinished.md) — MVP1 sign-off with 10-section scorecard
- [`doc/deployment.md`](doc/deployment.md) — Free-tier deployment guide (Render + Neon + Oracle Cloud)
- [`doc/handover.md`](doc/handover.md) — Developer handover & architecture decisions
- [`Readme.md`](Readme.md) — Quick start, features, project structure
- [`doc/design.md`](doc/design.md) — System design + MVP1/MVP2 scope

### 🧪 Tests

- 218 pytest tests across 21 test files
- 96% backend coverage
- Whisper + Ollama mocked in unit tests; integration tests marked `@pytest.mark.slow`
- Regression tests for: mindmap ancestor walking, mindmap drag/pan, dashboard upload, sidebar search, **account switching**

### ⚠️ Known limitations (intentional, deferred to MVP2)

- Single-user only (no multi-tenant auth)
- Synchronous LLM/Whisper blocks the request (no Celery yet)
- SQLite loses data on filesystem wipe (use PostgreSQL on Render)
- Files in `uploads/`/`storage/` are not backed up (use S3 in MVP2)
- No Alembic — schema changes require a wipe
- `yt-dlp` URL downloader is a **future** feature, not MVP1

---

## Versioning

This project uses [Semantic Versioning](https://semver.org/):
- **Major** (X.0.0) — breaking changes to the public API
- **Minor** (1.X.0) — new features, backwards-compatible
- **Patch** (1.0.X) — bug fixes, backwards-compatible

Tags are signed (`git tag -a v1.0.0 -m "..."`) and pushed with `git push origin v1.0.0`.

---

## [2.0.0] - 2026-07-11 — MVP2.0 (in progress, partial)

🚧 **Pre-release.** MVP2.0 is feature-complete for the "auto-pipeline" pillar
on branch `MVP2.0` (20 commits ahead of `main`, all pushed). The "i18n /
Alembic / Celery" pillars are still in design or pending. See
[`doc/MVP2.0-Status.md`](doc/MVP2.0-Status.md) for the live status table.

**Stats (as of 2026-07-11):** 491 tests passing · 87% backend coverage · 31 new commits · 0 regressions

### ✨ Features

- **Auto-transcribe + auto-generate on upload** (`7e70fe3`, `b4d612f`) — no more "click transcribe, wait, click generate, wait again". Upload → fully ready. 4-video bulk test: 4 files → 4 ready without any user click.
- **Multi-file / non-blocking bulk upload** — drag in 20 files, walk away. 2 GB per-file cap, real-time progress per file.
- **Natural sort by leading number** (`dd35bc1`) — "1.-foo" sorts before "10.-bar". Default ascending, with a sort button on each section header.
- **Session-expiry redirect on protected SSR routes** (`814a98a`, `2b40fc9`, `67d980b`) — visiting `/dashboard` after the Firebase session cookie expires redirects to `/login?next=...`.
- **Retry script for failed generate jobs** (`f37f7a0`) — `python scripts/retry_failed_generate.py --dry-run` to preview, no flag to actually run. Used to recover 13/13 broken videos from the 0-byte upload incident.
- **Retry-all-failed button on the section header** (`3bb256b`, `162c85d`) — one click re-queues every failed video in the section. Spinner + "Retrying N (X transcribe, Y generate)" toast.
- **Transcript export endpoint** (`a1235b2`) — `GET /api/videos/{id}/transcript/export?format=md|json|txt`. Returns the transcript as Markdown / JSON / plain text with proper RFC 5987 unicode filenames.
- **Download transcript button on the video page** (`72ae0bc`) — format selector + download button next to the transcript header. Filename = the video's title (matches the original upload filename minus extension).
- **Export filename cleaned** (`b026d81`) — strip ugly runs of underscores (e.g. `____` from Bilibili auto-renames) from the exported transcript filename, but leave the original DB title alone.
- **Whole-video chat (Discuss tab)** (`b20584a`) — 5th tab on the video page. AI gets the full transcript + summary + mindmap + quiz as the system prompt. Sessions persisted in the same `ChatSession` table with `scope='video'`. `/chat-history` shows VIDEO/FLASHCARD badges to tell the two types apart.
- **Video / section / course delete** (`40d8c4a`, `2213bc9`, `1acc4ea`, `6a881e9`) — hard-delete cascades with file unlink + asset + chat-session cleanup. Each endpoint returns a summary `{status, deleted: {file, files, assets, chat_sessions}}` for a meaningful toast. Frontend confirmation modal on the video page header, each section header, and each course card on the dashboard. Soft-delete / trash / restore is deferred to MVP3 (see `doc/manualTodo.txt` #8).
- **10 GB upload cap** (`e5db159`) — raised `MAX_FILE_SIZE` from 2 GB to 10 GB (inclusive) per manual todo [jul11] #3.
- **"ready · 9:08" timing badge** (`ae4df7d`) — every video's status badge on the course page now shows how long it took from upload to ready, e.g. `ready · 9:08` or `ready · 2:05:33` for > 1 hour. Stamped by the transcribe + generate workers, formatted via a new `format_duration` Jinja filter. **(Superseded in [2.0.4] — now renders `ready · T:M:SS, G:M:SS` for videos with all three timestamps; legacy videos keep the single-time format.)**
- **Whisper model picker with smart picks (plumbing)** (`2a96049`, `1497cd7`) — adds 2 new options to the model dropdown: "Local best and fast" (Distil-large-v3 via faster-whisper) and "Local best and extremely fast" (MLX Whisper with distil-large-v3, Apple Silicon only). UI uses an `<optgroup>` dropdown; default is platform-dependent (MLX smart pick on M-series, faster-whisper smart pick elsewhere). `transcribe_with_backend()` dispatches by backend with auto-fallback. The actual MLX call is a follow-up commit (it raises `NotImplementedError` until `mlx-whisper` is wired up). New DB columns track the resolved backend + model per video so the UI can show what actually ran.

### 🐛 Bug fixes

- **Bulk-upload route shadowing** (`dbeed50`) — `POST /{video_id}/transcribe` was declared before `POST /upload-bulk/{section_id}`, shadowing it. Bulk upload returned 404 "Not Found". Fixed by route reordering + a structural regression test.
- **LLM JSON parser hardening** (`e8de51b`) — `glm-5.2:cloud` returns non-deterministic responses (sometimes `len=0`, sometimes prose-wrapped). Added strategy 3 (strip preambles) + better error message with raw response preview.
- **0-byte upload crashes auto-pipeline** (`eeab211`) — upload only checked the upper size bound. Empty files from cancelled uploads would crash Whisper with `[Errno 1094995529] Invalid data found when processing input`. Now returns 400 with a clear message.
- **"Retry N failed" button does nothing** (`162c85d`) — endpoint only looked at `last_generate_job.status='failed'`, not `last_transcribe_job.status='failed'`. Fixed by partitioning failures by step (transcribe / generate). 5th postmortem in `doc/Blockers.md`.
- **Transcript panel showing 00:10 instead of 00:00 on page load** — fixed in MVP2.0.0a. `offsetTop` is relative to body, not container. Fixed with `getBoundingClientRect`.
- **Video delete redirected to the wrong course** (`1951a20`, `00c8c84`) — frontend used `document.querySelector('a[href^="/course/"]')` to find the redirect target, but that grabbed the first course link on the page (sidebar/course list), not the video's actual course. Fixed by rendering `courseId` as a JS constant and using it directly.
- **Section delete click did nothing** (`5f38435`) — root cause was a missing `}` for the `uploadVideo` function (introduced in `7e70fe3`), which made the entire course page `<script>` block a syntax error. Browsers don't run a script with a syntax error at all, so *every* page JS (toggleSection, retryAllFailed, showDeleteSectionModal, …) was dead code. Fixed by adding the missing brace. Also added a regression test (`test_course_page_inline_script_parses_cleanly`) that reads the template source and asserts brace+paren balance, so any future script-syntax regression fails the test suite.

### 📚 Docs

- [`doc/MVP2.0-Status.md`](doc/MVP2.0-Status.md) — **NEW** — single-page status snapshot for MVP2.0
- [`doc/MVP2.0-first-designQuestions.md`](doc/MVP2.0-first-designQuestions.md) — design doc with Milestones table, Appendix A (deferred MVP3 modes), Appendix B (duplicate detection B1–B5)
- [`doc/Blockers.md`](doc/Blockers.md) — 5 postmortems, all RESOLVED
- [`doc/HowToStart.md`](doc/HowToStart.md) — **NEW** — full backend startup guide
- [`scripts/stop.sh`](scripts/stop.sh), [`scripts/status.sh`](scripts/status.sh) — **NEW** helpers

[2.0.0]: https://github.com/yuanfengli168/video-learning-app/compare/v1.0.0...MVP2.0
[1.0.0]: https://github.com/yuanfengli168/video-learning-app/releases/tag/v1.0.0

## [2.0.1] - 2026-07-14 — MVP2.0 Part A (anti-drift language policy)

🎯 **One bug, one feature, one number.** Fixes the "Thank you" hallucination
loop on long Mandarin audio (and other languages) by **locking whisper's
language for the whole file** and adding **anti-drift kwargs**.

**Proven result**: on a 2.5h Mandarin video, the same file went from
**~0% Mandarin output → 97.8% Mandarin output** — a clean, single-transcript
result with no `condition_on_previous_text` chain.

### ✨ Features

- **Language dropdown on the video page** (`08c118d`) — three options: `Auto` /
  `English` / `中文`. When the worker has already auto-detected a language, the
  UI shows a `Detected: 中文` label so the user knows what was inferred. The
  dropdown ships a confirmation modal so accidental language changes don't
  silently re-transcribe an already-correct transcript.
- **Backend `language=` param wired end-to-end** — new
  `app/services/transcription.py::INITIAL_PROMPTS` + `get_initial_prompt()`
  + `LANGUAGE_CHOICES` + `LANGUAGE_LOCKED_CODES`. Locked codes: `zh`, `en`,
  `auto`. Each locked code maps to a bias prompt:
  - `zh` → `"以下是普通话的对话。"`
  - `en` → `","` (the comma-only English bias prompt)
  - `auto` → no bias, no `language=` arg (let whisper decide)
- **Auto-detection in the worker** (`08c118d`) — when a video has
  `videos.language IS NULL`, the worker samples 20 windows with
  `faster-whisper`'s `detect_language` and locks to the first language whose
  speech probability exceeds the configured threshold (default 50%). Settings
  exposed via `LANGUAGE_DETECT_SAMPLE_WINDOWS` + `LANGUAGE_DETECT_SPEECH_THRESHOLD`
  in `.env`.
- **MLX-whisper path now actually dispatches** (`08c118d`) — the placeholder
  `NotImplementedError` from commit `2a96049` (whisper-model-picker Part A)
  is replaced with a real call. Tests in `test_whisper_picker.py` updated to
  verify the new behavior (3 new tests).

### 🐛 Bug fixes

- **`fix(transcription):` "Thank you" loop on long Mandarin** — root cause
  was the default `condition_on_previous_text=True` chaining the first
  30 seconds of hallucination ("Thank you. Thank you for watching. ...")
  across every subsequent 30-second window. The fix is
  `condition_on_previous_text=False` + `compression_ratio_threshold=1.8`
  (rejects any segment whose output is too repetitive, a classic
  hallucination signature) + locking the language with `language="zh"` so
  the model never drifts to English in the middle of a Mandarin file.
- **Post-transcript worker `NOT NULL` constraint** on `videos.language` —
  the new column is added with a default of `NULL` for existing rows
  (so the migration is non-destructive), and the worker writes the resolved
  language back so re-runs don't re-detect.

### 📚 Docs

- [`doc/HowToStart.md`](doc/HowToStart.md) — workspace path refreshed to
  `~/Desktop/Githubs/video-learning-app/` (the OneDrive path is no longer
  used). Test count updated to 487 passing.
- `app/config.py` — 2 new env settings documented inline.

### 🧪 Tests

- 487 passing, 12 pre-existing failures (no new regressions)
- 3 new tests in `tests/test_whisper_picker.py`:
  - `test_transcribe_with_backend_mlx_path_calls_mlx_whisper`
  - `test_transcribe_with_backend_mlx_path_passes_language`
  - `test_transcribe_with_backend_mlx_path_no_language_when_auto`
- 1 test rewritten: the old `test_transcribe_with_backend_mlx_path_raises_not_implemented`
  is replaced by the three above (the `NotImplementedError` is gone).

[2.0.1]: https://github.com/yuanfengli168/video-learning-app/compare/97c4e4d...08c118d

## [2.0.2] - 2026-07-14 — Discuss tab clickable timestamp citations

🎯 **One feature.** When the AI cites a moment in the video inside the
💬 Discuss tab, the timestamp now becomes a clickable link that
seeks the video to that moment and highlights the matching
transcript line(s) — same UX as clicking a mindmap node.

**Proven result:** the AI's response `[3:45] 视频提到 Claude Code
需要付费` now renders as a styled inline button; clicking it
seeks the video to 3:45 and highlights the relevant transcript
lines for context.

### ✨ Features

- **Inline clickable citation links in the Discuss tab** —
  the backend parses `[M:SS]` / `[H:MM:SS]` markers from the
  AI's response and returns them as a structured `citations`
  list; the frontend renders each as a small styled button
  (same look as the mindmap nodes). Clicking the button:
  1. Seeks the video to that moment
  2. Highlights the matching transcript line(s) for context
  3. Leaves the user on the Discuss tab so they can keep
     reading the response
- **One source of truth for the citation format** — the
  `app/services/chat.py:parse_citations()` function. Two clean
  regexes (M:SS and H:MM:SS) replace the previous ambiguous
  single-regex attempt. Fractional seconds (e.g. `[1:23.5]`)
  are preserved (not rounded to int).
- **Improved system prompt** — VIDEO_CHAT_SYSTEM_PROMPT now
  explicitly documents the `[M:SS]` citation format with
  examples and asks the LLM to (1) quote a short verbatim
  snippet alongside the timestamp so the user can verify,
  (2) be honest when the transcript doesn't cover the topic.
- **Client-side fallback parser** — `renderDiscussTextWithCitations`
  falls back to a client-side regex parse if the backend's
  `citations` list is missing (e.g. for messages loaded from
  the chat history page where the original API response was
  discarded).

### 🐛 Bug fixes

- **Silent transcript-parse failure no longer confuses the AI**
  — previously the chat endpoint silently swallowed the
  JSON parse error in `_build_video_chat_context` and told
  the LLM "(Transcript present but could not be parsed.)",
  which made the AI hallucinate explanations for why the
  transcript didn't exist (manualTodo [jul14] #6). Now we:
  1. Log the underlying error + a snippet of the failing
     content to the server console
  2. Give the LLM a clear message so the AI can tell the
     user "your transcript exists but my parser couldn't
     read it — try re-transcribing" instead of inventing
     a reason

### 📚 Docs

- [`doc/MVP2.0-Status.md`](doc/MVP2.0-Status.md) — feature
  recorded in the live status doc (was already tracked as
  the MVP3.0 Part B planned work)

### 🧪 Tests

- 523 passing, 12 pre-existing failures (no new regressions)
- +23 new tests in 3 files:
  - `tests/test_chat_service.py` (+14): parse_citations
    regex coverage — empty input, single M:SS, multiple
    M:SS, H:MM:SS, mixed, leading zero, fractional seconds,
    range rejection, tilde rejection, trailing-s rejection,
    invalid minutes/seconds, unrelated brackets, offset
    preservation for splice-points, ordering guarantee
  - `tests/test_chat_router.py` (+6): response-shape
    coverage — video-scope response includes empty
    `citations` list, M:SS is parsed, H:MM:SS is parsed,
    multiple markers all parsed in order, flashcard-scope
    has empty `citations` (no transcript to cite from)
  - `tests/test_ui_features.py` (+3): video-page UI
    presence — Discuss tab + send handler, citation
    renderer + client-side fallback regex, system prompt
    documents the citation format
- `app/services/chat.py`: 96% coverage
- `app/routers/chat.py`: 97% coverage

[2.0.2]: https://github.com/yuanfengli168/video-learning-app/compare/cc42b15...HEAD

## [2.0.3] - 2026-07-14 — Tab switching single-select fix

🐛 **One-line bug fix.** Clicking Summary / Flashcards / Quiz /
Mindmap while the Discuss tab was open left the Discuss panel
visible underneath — two panels showed at once. The fix adds
`'discuss'` to the `switchTab()` forEach loop so all five
panels are mutually exclusive (commits `dc11d5f`, `d7c4ef6`).

### 🐛 Bug fixes

- **Tab switching single-select violation** — `switchTab()` in
  `app/templates/video.html` iterated over the original 4
  tabs but not `'discuss'`. The Discuss tab was added in
  MVP2.0 (commit `b20584a`) but the iteration list was never
  updated, so clicking any other tab while Discuss was open
  left the Discuss panel visible.

### 🧪 Tests

- 526 passing, 12 pre-existing failures (no new regressions)
- +1 new test in `tests/test_ui_features.py`:
  - `test_video_page_switchTab_hides_all_five_panels` —
    reads the `switchTab` function body from the rendered
    page and asserts the forEach iterates over all 5 tabs.
    Verified to fail when the bug is present and pass with
    the fix.

[2.0.3]: https://github.com/yuanfengli168/video-learning-app/compare/369b111...HEAD

## [2.0.4] - 2026-07-15 — Per-step transcribe/generate timing

⏱️ **Bug fix + feature.** The course page badge now shows the
**actual transcribe time** and **actual generate time** as
separate numbers (`T:0:55, G:0:44`) instead of the misleading
`created_at` → `generated_at` wall-clock duration (which
included the bulk-upload queue wait and made video #34 of a
34-video batch show `36:55` instead of its real ~55s of
transcribe time).

**Proven result:** video #34 of a 34-video batch, which
queued for ~36 min behind the other 33 videos, now shows
`ready · T:0:55, G:0:44` — the real processing time, not the
queue + process time.

### ✨ Features

- **`transcribe_started_at` timestamp** — new nullable
  `DateTime` column on `videos` (additive migration; legacy
  rows stay NULL). Stamped at the very top of
  `_run_transcribe_job`, BEFORE `WhisperModel.transcribe()` is
  called, so the duration includes the model load time.
  Re-stamped on every fresh transcribe run (manual retry, etc.)
  so the badge always reflects the most recent transcribe work.
- **Per-step duration badge** — `app/templates/course.html`
  now shows `ready · T:M:SS, G:M:SS` for videos that have all
  three timestamps (`transcribe_started_at`, `transcribed_at`,
  `generated_at`). Legacy videos with `transcribe_started_at
  IS NULL` still fall back to the old `created_at` →
  `generated_at` duration, so no rows are visually broken.

### 🐛 Bug fixes

- **Misleading `ready · 36:55` badge for batch uploads** —
  previously, the course page computed the duration as
  `generated_at - created_at`, which for batch-uploaded
  videos included the queue wait behind earlier videos in the
  batch. A 55s transcribe that queued for 36 min showed as
  `36:55` — users thought the transcribe itself was broken.
  Now the badge shows the real transcribe and generate times
  separately, and the queue wait is invisible (it was never
  the transcribe's fault to begin with).

### 🧪 Tests

- 532 passing, 12 pre-existing failures (no new regressions)
- +6 new tests in `tests/test_per_step_timing.py`:
  1. `test_video_model_has_transcribe_started_at_column` —
     schema-level check that the new column exists and is
     nullable.
  2. `test_transcribe_started_at_migration_registered` —
     verifies the additive migration entry is registered in
     `app/database.py:_MIGRATIONS`.
  3. `test_transcribe_worker_stamps_started_at_before_whisper_loads` —
     the core regression test. Mocks `faster_whisper.WhisperModel`
     and asserts the stamp happens BEFORE the model is called.
     Confirmed to fail when the stamp is removed.
  4. `test_course_page_renders_both_per_step_times` — verifies
     the new `T:...,G:...` format is rendered.
  5. `test_course_page_legacy_fallback` — verifies the legacy
     `created_at` → `generated_at` fallback still works.
  6. `test_course_page_hides_timing_for_non_ready_status` —
     verifies the badge is hidden for non-ready statuses.

[2.0.4]: https://github.com/yuanfengli168/video-learning-app/compare/4573812...HEAD

## [2.0.5] - 2026-07-15 — Bulk upload 400 "error when parsing the body" fix

🐛 **Bug fix + UX improvement.** Uploading 3+ files at 1+ GB
to the bulk endpoint returned a 400 Bad Request that the
user saw as a cryptic "error when parsing the body". The
real cause was a 3-layer failure that the user couldn't see
through.

**The chain that produced the error:**

1. **uvicorn/h11**: the HTTP/1.1 receive buffer is capped at
   16 KB by default (`h11._connection.DEFAULT_MAX_INCOMPLETE_EVENT_SIZE`).
   For a multi-GB multipart body, the buffer can briefly
   exceed 16 KB between `next_event()` calls, triggering
   `h11.RemoteProtocolError` ("Receive buffer too long").
2. **uvicorn**: catches that error and returns a plain-text
   400 with body `"Invalid HTTP request received."` — no JSON,
   no `detail` field.
3. **Frontend**: calls `await resp.json()` on the plain-text
   response, which throws `SyntaxError: Unexpected token I in
   JSON at position 0`. The user sees this surfaced as
   "Bulk upload error: Unexpected token I in JSON at
   position 0", which the user paraphrased as "error when
   parsing the body" (the "parsing" they refer to is the JS
   `JSON.parse`, not the server's multipart parser).

**The fix (3 layers):**

- **Server, layer 1 — uvicorn flag**: bump
  `--h11-max-incomplete-event-size` from 16 KB to 64 MB in
  `scripts/start.sh`. This prevents the 16 KB buffer from
  triggering for realistic upload sizes (10 GB max per file,
  well under 64 MB).
- **Server, layer 2 — global exception handlers**: add
  `@app.exception_handler(StarletteHTTPException)` and
  `@app.exception_handler(Exception)` in `app/main.py`.
  These ensure ANY uncaught error during request processing
  is returned as a proper JSON `{"detail": "..."}` response
  — never plain text, never HTML tracebacks.
- **Frontend — `safeJsonParse()` helper**: add a global
  `safeJsonParse(resp)` helper in `app/templates/base.html`
  that defensively checks `Content-Type` and falls back to
  `resp.text()` if the response isn't JSON. Update
  `dashboard.html` and `course.html` upload handlers to use
  the helper.

### 🐛 Bug fixes

- **Misleading "error when parsing the body" on bulk upload**
  — the user couldn't tell whether the error was the server's
  fault, their own network, or browser-side. Now they see the
  actual server error (e.g. "File too large. Max size: 10 GB")
  in a clear alert, with a "(server)" or "(network)" prefix
  to disambiguate.

### 🧪 Tests

- 540 passing (was 532, +8 new tests in
  `tests/test_bulk_upload_error_handling.py`):
  1. `test_starlette_http_exception_handler_returns_json` —
     verifies HTTPExceptions return JSON.
  2. `test_unhandled_exception_handler_is_registered` —
     structural check that the catch-all Exception handler
     is on the app.
  3. `test_bulk_upload_route_returns_json_on_404` — verifies
     the bulk endpoint returns JSON for 404s.
  4. `test_base_html_contains_safeJsonParse_helper` —
     structural check that the helper is defined in base.
  5. `test_dashboard_uses_safeJsonParse_for_bulk_upload` —
     structural check that the dashboard uses the helper.
  6. `test_course_uses_safeJsonParse_for_bulk_upload` —
     structural check that the course page uses the helper.
  7. `test_start_sh_bumps_h11_max_incomplete_event_size` —
     structural check that the uvicorn flag is set to
     ≥ 1 MB (default is 16 KB).
  8. `test_all_upload_handlers_use_safeJsonParse` —
     guards against future regressions if someone adds a new
     upload endpoint without using the helper.

[2.0.5]: https://github.com/yuanfengli168/video-learning-app/compare/5bd11a4...HEAD

## [2.0.6] - 2026-07-15 — Logout still sees summary (manualTodo [jul14] #1)

🐛 **Bug fix.** When a user logged out (or had no session
cookie), hitting a deep link like `/video/{id}`,
`/course/{id}`, or `/chat-history` would render the page
shell but every data API call silently 401'd in the
background. The user saw a "phantom" page with no
explanation.

**Proven result:** an anonymous user can no longer see a
half-rendered video page. They get redirected to the
dashboard with the existing "session expired" toast, which
makes the issue visible instead of silent.

### 🐛 Bug fixes

- **Phantom SSR pages on protected routes** — the
  `SessionExpiryMiddleware` previously only redirected
  *present-but-invalid* cookies. Anonymous visits (no
  cookie) were let through, and the page rendered a
  half-broken shell. Now they redirect to
  `/?session=expired` like the expired-cookie case. The
  dashboard's special-case ("anonymous visitors see the
  Sign in prompt") is preserved. Manual todo [jul14] #1
  "logout but still can see summary".

### 🧪 Tests

- 543 passing (was 540, +3 new tests in
  `tests/test_session_expiry_middleware.py`):
  1. `test_protected_video_route_with_no_cookie_redirects` —
     `/video/{id}` with no cookie now redirects.
  2. `test_protected_course_route_with_no_cookie_redirects` —
     `/course/{id}` with no cookie now redirects.
  3. `test_protected_chat_history_route_with_no_cookie_redirects` —
     `/chat-history` with no cookie now redirects.
- `test_dashboard_with_no_cookie_is_not_redirected` (new
  positive-case test) — verifies the dashboard still lets
  anonymous visitors through (they see the "Sign in" prompt).
- Updated `tests/conftest.py` `client` fixture to set a
  default valid session cookie, so all existing tests that
  exercise protected routes keep working. Tests that
  explicitly test the no-cookie path (the 4 new ones above
  + 2 existing ones) call `client.cookies.clear()` to
  override the default.

[2.0.6]: https://github.com/yuanfengli168/video-learning-app/compare/eaf32d4...HEAD

## [2.0.7] - 2026-07-15 — Remove distil smart picks; rename turbo

🎨 **UI cleanup + UX fix.** The model dropdown's "Smart picks
(recommended)" group previously had 2 distil-large-v3 options
(`local-best-and-fast`, `local-best-and-extremely-fast`)
plus the new `local-large-turbo`. The distil options are
removed because distil-large-v3 is English-biased and
ignores the `language="zh"` lock — it was producing
all-English hallucination loops on Chinese videos. The
only smart pick that remains is `local-large-turbo`
(MLX Whisper Large V3 Turbo), which is multilingual and
is now both the default and the only smart pick.

**Proven result:** the dropdown now shows 5 options
(4 manual + 1 smart), the smart option has a proper
user-facing name ("MLX Whisper Large V3 Turbo
(recommended)"), and bulk uploads use this option by
default. The previous "(MLX, M-series, multilingual)"
label was descriptive but didn't name the model or
signal "recommended".

### ✨ Features

- **Proper name for `local-large-turbo`** — the
  user-facing label is now "🚀 MLX Whisper Large V3
  Turbo (recommended)" (was "🚀 Local Large-v3 Turbo
  (MLX, M-series, multilingual)"). Shorter, identifies
  the engine (MLX), names the model (Whisper Large V3
  Turbo), and signals the recommended status.

### 🐛 Bug fixes

- **Bonus fix caught by the test sweep**: the
  `switchTab()` function in `app/templates/video.html`
  was a latent regression from MVP2.0.2 (commit
  `dc11d5f`). The commit added a "Hide all five tab
  panels" comment but forgot to actually add `'discuss'`
  to the forEach loop, so the Discuss tab stayed
  visible when other tabs were clicked. The
  `test_video_page_switchTab_hides_all_five_panels`
  test caught this. Fixed by adding 'discuss' to the
  forEach.

### 🧪 Tests

- 540 passing (was 543, -3 because the 2 distil-related
  tests were replaced/removed; net -3).
- All `tests/test_whisper_picker.py` tests updated to
  reflect the new model structure (5 entries instead
  of 7, 1 smart pick instead of 3).
- 3 new regression tests verify the registry has the
  expected structure; they FAIL if the distil entries
  are restored.
- `test_video_page_switchTab_hides_all_five_panels`
  catches the latent tab-switching regression.

### Migration notes

- The 2 distil entries are **commented out** in
  `MODEL_REGISTRY`, not deleted. They can be restored
  by uncommenting if needed (e.g. for an English-only
  workload where distil-large-v3 is genuinely faster).
- The non-MLX default changed from
  `local-best-and-fast` (distil) to `base` (the
  recommended manual pick). On Apple Silicon, the
  default is unchanged (`local-large-turbo`).
- Existing videos already on disk are unaffected —
  they have a `whisper_model` value that points to
  their original choice. Only new uploads use the
  new defaults.

[2.0.7]: https://github.com/yuanfengli168/video-learning-app/compare/748a557...HEAD

## [2.0.8] - 2026-07-15 — Collapsible section-videos panel on video page

🎨 **UI feature.** The video page now has a collapsible
panel above the tabbed interface (Summary / Flashcards /
Quiz / Mindmap / Discuss) that shows the current section's
video list. The user can:
- Click any video in the list to switch to it (no need to
  navigate back to the course page).
- Sort by name (asc/desc) or date (asc/desc) via a
  dropdown.
- The panel's open/closed state AND the sort direction
  persist in `localStorage`, so the user's choice
  survives page reloads.
- The currently-playing video is visually highlighted
  with a left border + indigo background.

**Proven result:** on a 3-video section, the user can
go from video #1 to video #3 in 1 click (was 2 clicks:
back to course → click video #3).

### ✨ Features

- **Section-videos panel on the video page** — collapsible
  `<details>` element above the tabs. Each video shows
  its title, status badge, and a link to its video page.
  The user clicks any video in the list to switch to it in
  1 click (no need to navigate back to the course page).
- **Sort dropdown** — 4 options: Name ↑, Name ↓, Date
  ↑, Date ↓. Client-side sort using pre-computed
  `data-sort-key` (natural sort, e.g. "1. intro" before
  "10. conclusion") and `data-date` (ISO 8601) attributes.
- **localStorage persistence** — `videoPageSectionVideos_
  <section_id>_sort` and `_open`. Survives page reloads.
- **Current-video highlight** — the playing video has
  `bg-indigo-50` + `border-l-4 border-indigo-500` in the
  panel list. The user never gets lost.

### 📝 Amendment (2026-07-15, same day)

User feedback right after the 2.0.8 ship: the panel is
for **quick context switching**, not status reporting. The
per-step timing badge (`T:0:55, G:0:44`, from §18) is
**removed from the panel** — the user only needs the plain
status word (`ready` / `error` / `transcribing` / etc.) to
decide which video to click. The timing badge is kept
on the **course page** (where users scan the full section
and want to see processing times) — see
`app/templates/course.html:74`. Same-version amendment;
no version bump.

- **Panel status badge is plain** — just the status
  word. No `T:..., G:...` suffix.
- **Course page status badge is unchanged** — still
  shows the per-step timing when available.
- New regression test
  `test_section_videos_panel_omits_per_step_timing_badge`
  verifies the panel doesn't render `T:` / `G:`.
- Companion test
  `test_course_page_still_shows_per_step_timing_badge`
  guards against an over-zealous cleanup that might
  also strip the badge from the course page.

### Implementation notes

- **No new endpoint** — `section.videos` is already in
  the template context via the existing `video_view()`
  route. The user explicitly chose this over a new
  endpoint.
- **No next/previous buttons** — the user explicitly
  skipped those for now. The video list serves the same
  purpose (switching videos) without the keyboard-
  navigation complexity.
- **Reuses the existing `natural_sort_key_str` Jinja
  filter** (added in MVP3.0 #2) — no new sort logic
  needed.
- **Mirrors the existing course-page sort pattern** —
  same `data-sort-key` attribute, same `natural_sort_key`
  comparison. Two pages (course + video) now share a
  consistent sort UX.

### 🧪 Tests

- 552 passing (was 540, +12 new tests in
  `tests/test_video_page_section_panel.py`:
  1. `test_section_videos_panel_renders` — the panel
     `<details>` element is present.
  2. `test_section_videos_panel_shows_all_videos` — the
     panel lists every video in the section.
  3. `test_section_videos_panel_current_video_highlighted`
     — the playing video has the highlight classes.
  4. `test_section_videos_panel_data_sort_key_present` —
     each `<a>` has a `data-sort-key` attribute.
  5. `test_section_videos_panel_data_date_present` —
     each `<a>` has a `data-date` attribute.
  6. `test_section_videos_sort_dropdown_present` — the
     `<select>` has all 4 sort options.
  7. `test_section_videos_empty_state_message` — handles
     1-video edge case.
  8. `test_section_videos_panel_collapsed_by_default` —
     the panel doesn't have `open` attribute on first
     load.
  9. `test_section_videos_sort_function_present` — the
     `sortSectionVideos()` JS function is defined.
  10. `test_section_videos_localstorage_keys_present` —
      the localStorage prefix is in the script.

  7 of the 10 tests are verified to FAIL when the panel
  is removed (regression test).

[2.0.8]: https://github.com/yuanfengli168/video-learning-app/compare/6eff8d7...HEAD

## [2.1.0] - 2026-07-18 — Plugin Tools tab + WebM→MP4

🎨 **UI feature.** A new "🛠️ Tools" tab on the video
page, listing the available plugins from
`PLUGIN_REGISTRY`. v1 ships with one plugin: **Convert to
MP4 (H.264 + AAC)** — transcodes the current video via
`ffmpeg` to a more browser-compatible format. The new
file is written **side-by-side** with the original (the
original WebM is never touched).

**Key design:**
- **Plugin registry** (`app/services/plugins.py`) — a
  dict mapping plugin keys to `PluginSpec` dataclasses.
  Adding a new plugin = adding one entry to the dict.
  No install/upgrade flow, no security audit, no
  path-traversal risk.
- **Plugin Run audit log** (`plugin_runs` table) — every
  invocation writes a row with `ok`, `message`,
  `output_path`, `extra_json`, `created_at`. CASCADE-
  deleted with the parent video. The UI can show "last
  transcode: 2 hours ago, 1.2 GB MP4 written" from this
  log (future enhancement, not in v1 UI).
- **Side-by-side transcode** (not in-place) — the
  original WebM is never modified, per the user's
  explicit choice (safer default; user can delete the
  original via the existing Delete Video button).
- **ffmpeg detection** — `is_ffmpeg_available()` is
  checked both at page load AND per-run. The Run
  button is rendered disabled with a "Missing system
  dependency" warning if ffmpeg isn't on `$PATH`. The
  warning includes the exact install command for the
  user's OS.

**New endpoints:**
- `GET  /api/plugins` — list available plugins (with
  availability info for the UI)
- `POST /api/plugins/{name}/run?video_id=<uuid>` — run a
  plugin on a video (synchronous for v1; will be
  BackgroundTasks'd in MVP2.1.1 alongside the worker pool)
- `GET  /api/plugins/runs/{run_id}` — fetch a run's
  status (used by the UI to poll long-running plugins;
  not needed for v1's WebM→MP4 which is synchronous)

**Stats:** 552 → 583 tests passing (+31), 92% coverage
maintained, 0 regressions in the existing 552 tests.

**Proven result:** the WebM→MP4 happy path is verified
by `test_transcode_actually_runs_ffmpeg_on_real_file`
which generates a 1-second test pattern WebM via
ffmpeg, transcodes it, and asserts the output MP4
exists and is non-empty (skipped when ffmpeg isn't
installed, but passes when it is).

### Files changed

- `app/services/plugins.py` (new, 195 lines) — the
  `PLUGIN_REGISTRY` + `PluginSpec` + `PluginResult` +
  `transcode_webm_to_mp4()` + `is_ffmpeg_available()`
- `app/models/plugin_run.py` (new, 60 lines) — the
  `PluginRun` audit log model
- `app/models/__init__.py` — register `PluginRun`
- `app/models/video.py` — add `plugin_runs` relationship
  to `Video` (cascade-delete)
- `app/database.py` — import the new model so
  `create_all()` picks it up
- `app/routers/plugins.py` (new, 115 lines) — the
  `GET/POST /api/plugins` router
- `app/routers/frontend.py` — pass
  `available_plugins` to the video page template
- `app/main.py` — register the new router
- `app/templates/video.html` — add the Tools tab button
  + Tools tab content panel + `runPlugin()` JS function

### Tests

4 new test files, 31 new tests:
- `tests/test_plugin_registry.py` (10 tests) —
  registry shape, key uniqueness, URL-safety, ffmpeg
  detection. The "registry has exactly the v1 plugins"
  test is the contract test for future plugin additions.
- `tests/test_webm_to_mp4_plugin.py` (8 tests) —
  ffmpeg-missing, source-missing, ffmpeg-error,
  timeout, real ffmpeg happy path, audit log row,
  unknown-key audit, exception swallow.
- `tests/test_tools_tab_rendering.py` (7 tests) —
  Tools tab button, content panel, plugin card, Run
  button, ffmpeg-missing disabled state, `runPlugin()`
  JS function presence.
- `tests/test_plugin_endpoints.py` (6 tests) — list
  endpoint, run endpoint, 404s, audit log row, get
  run by id.

[2.1.0]: https://github.com/yuanfengli168/video-learning-app/compare/v2.0.8...HEAD

## [2.1.0.1] - 2026-07-19 — Tools tab UX fixes + background worker pool

🎯 **3 UX fixes + 1 backend architectural change.** The
Tools tab's "Re-Upload with MP4" button now appears
**immediately** after a successful transcode (no page
refresh needed), the swap action **doesn't reload the
page** (it just swaps the video element's src), and
plugin runs (WebM→MP4) now run in a **background
worker pool** — closing the tab no longer kills the
transcode.

**Proven result:** the user's "0:02" bfcache bug is
gone — after a swap, the player shows the new MP4's
duration + controls in <100ms with no `Cmd+Shift+R`
required. And a 30-min transcode that the user kicks
off + closes the tab on now continues in the server
process; the user can reopen the page and see the
result.

### ✨ Features

- **Re-Upload button visible immediately after Run** — the JS
  `refreshLastRun()` template now mirrors the server-rendered
  version, including the "Re-Upload with MP4" button. Before
  this fix, the button only appeared on the next page reload
  (because the JS template was built without it). Extracted
  as a `renderSwapButton()` helper so the two paths stay in
  lockstep.
- **`videoStatus` exposed to JS** — a new
  `const videoStatus = '{{ video.status }}';` in the
  video page's script context. The JS `renderSwapButton()`
  helper uses this to enable the swap button only when
  the video is in `'ready'` state (matching the server-side
  conditional in the Jinja template).
- **No page reload after Re-Upload** — `performSwap()` now
  updates the `<video>` element's `src` in place with a
  cache-bust query param (`?v=${Date.now()}`) and calls
  `video.load()`. Replaces the old `setTimeout(location.reload,
  800)` flow, which had two problems:
  1. **Slow** — 800ms delay + page reload (typically
     300-500ms).
  2. **bfcache stale state** — the browser's
     back/forward cache can restore the previous page
     state (including the WebM video element with
     `currentTime=0:02`), even after `location.reload()`.
  The cache-bust query param forces a fresh fetch, and
  `video.load()` re-reads the new file's metadata so the
  duration + 3-dots menu render correctly.
- **Plugin worker pool** — new `app/workers/plugin_pool.py`
  with `asyncio.Queue` + `asyncio.Semaphore(3)`. The
  `POST /api/plugins/{name}/run` endpoint now returns
  **202 Accepted** with `{run_id, status: "queued"}`
  in <50ms (was 200 + full result, blocked for 2-5
  minutes for a typical 1-hour WebM transcode). The
  worker pulls jobs off the queue, runs them in
  parallel (up to 3 at once), and updates the
  `plugin_runs.status` field (`queued` → `running` →
  `done` / `failed`). The UI polls
  `GET /api/plugins/runs/{id}` every 1.5s to show
  progress; closing the tab no longer cancels the
  job.
- **Plugin run status field** — new `status` column on
  `plugin_runs` (additive migration; legacy rows
  backfilled to `'done'` since they were always
  complete at insert time). Exposed in
  `GET /api/plugins/runs/{id}` and
  `GET /api/plugins/runs/by-video/{id}`.

### 🐛 Bug fixes

- **"Re-Upload with MP4" missing from JS-rendered last-run
  box** — the green success box rendered by
  `refreshLastRun()` after a Run only included the
  "Open in Finder" button, not the swap button. The
  server-rendered version (used on first page load)
  had both buttons. So users had to do a hard refresh
  to see the swap button after a successful transcode.
  Now both paths render the same set of buttons.
- **"0:02" stale WebM state after swap** — the player
  showed the old WebM's `currentTime=0:02` even after
  the swap. Root cause: `location.reload()` keeps the
  bfcache'd page state, including the `<video>` element.
  Fix: swap the `src` + call `load()` instead of
  reloading. The new MP4's duration + controls render
  immediately.

### 📝 Design notes

- **Worker pool, not background tasks** — the existing
  FastAPI `BackgroundTasks` mechanism is in-process
  but per-request: the request must stay open for the
  background work to be tracked. For long-running
  plugin runs (5+ min), the user closes the tab, the
  request is cancelled, the background task is killed.
  A dedicated `asyncio.Queue`-based pool with its own
  worker task survives the request lifecycle.
- **Limit = 3** — ffmpeg is CPU-bound; 3 parallel
  runs can use 3 cores without thrashing. Configurable
  via the `PluginPool(limit=...)` constructor argument.
- **Synchronous test mode** — `PluginPool.synchronous_mode
  = True` (set by the test `client` fixture) makes
  `submit()` run the plugin inline and update the row
  before returning. This lets tests assert on the
  result without polling, and sidesteps the singleton
  pool's worker task being bound to a closed event
  loop between tests.
- **Per-job DB session** — the worker opens its own
  SQLAlchemy session per job (the request's session
  is closed by the time the worker runs). This means
  the worker's commit and the worker's queries don't
  race the request's session.

### 🧪 Tests

- 614 passing (was 603, +11 new tests across 3 files)
- `tests/test_plugin_worker.py` (NEW, 8 tests) —
  status field transitions, 202 response shape, status
  in by-video endpoint, tab-close survival, pool stats,
  404 on unknown video, no duplicate rows in sync mode.
- `tests/test_tools_tab_rendering.py` (+3 tests) —
  `renderSwapButton()` helper defined, `videoStatus`
  exposed to JS, `performSwap()` uses
  `videoEl.src + videoEl.load()` instead of
  `location.reload()`.
- `tests/test_plugin_endpoints.py` (existing tests
  updated) — `test_run_plugin_writes_audit_log_row` and
  `test_get_run_returns_run_row` updated for the
  202 + status field responses; the worker pool's
  sync mode keeps these tests fast (no polling).

[2.1.0.1]: https://github.com/yuanfengli168/video-learning-app/compare/2.1.0...HEAD

## [2.1.0.2] - 2026-07-21 — Backlog bug fixes (3 small ones)

🐛 **Three user-discovered bugs, all fixed.** None
are user-visible until the conditions are right, but
all three were sitting in the code as latent
foot-guns. ~30 lines of code + 9 new tests.

### 🐛 Bug fixes

- **`Video.duration` column was declared as `Integer`
  but stored floats** — Whisper's segment-end timestamps
  are floats with sub-second precision (e.g. 336.44
  seconds for a 5:36 video). Storing 336.44 in an
  Integer column silently truncated to 336, losing
  440ms of accuracy in the course-page badge. The
  schema now declares `duration` as `Float` (REAL in
  SQLite, DOUBLE PRECISION in Postgres). Existing
  rows with integer values stay valid (SQLite
  happily casts int → float on read).
- **`Video.file_size` was NOT updated on swap** —
  after a WebM→MP4 swap, the DB still showed the
  original WebM's byte count (e.g. 54 MB) instead of
  the new MP4's (11 MB). Two user-visible consequences:
  the course-page size column lied, and the
  "are you sure?" delete prompt over-counted. The
  swap now `stat()`s the new file and stamps
  `video.file_size` before commit.
- **`get_video_file` hardcoded `Content-Type:
  video/mp4`** regardless of the actual file
  extension — .webm / .avi / .mov / .mkv / .m4v
  files were all served with the wrong MIME type.
  The fix maps extension → MIME type, with
  `application/octet-stream` as the fallback for
  unknown extensions (browser offers to download
  rather than play).

### 📝 Bonus fix (not a bug, but spotted while testing)

- **`extra_json` was stored as Python `str({...})`
  (single-quoted repr) instead of `json.dumps(...)`
  (double-quoted JSON)**. The audit log was technically
  valid Python but not valid JSON, which broke any
  external consumer that tried to parse it. Fixed in
  both places: `app/services/plugins.py:run_plugin`
  and `swap_video_file_to`.

### 🧪 Tests

- 623 passing (was 614, +9 new tests in
  `tests/test_backlog_bugs_2_1_0_2.py`):
  1. `test_video_duration_column_is_float` — verifies
     the model declares the column as Float
  2. `test_video_duration_stores_float_value` —
     verifies 336.44 round-trips correctly through
     the DB
  3. `test_swap_updates_file_size` — verifies
     `Video.file_size` matches the new file on disk
  4. `test_swap_audit_log_includes_size_info` —
     verifies the audit log's `extra_json` is valid
     JSON with both old + new sizes
  5-8. `test_get_video_file_returns_correct_mime_for_*`
     — verifies Content-Type for .mp4 / .webm / .mov
     / .mkv files
  9. `test_get_video_file_unknown_extension_falls_back`
     — verifies the application/octet-stream fallback

### Migration notes

- The `Video.duration` Integer → Float change is a
  SQLAlchemy type change but the underlying column
  type in SQLite is `NUMERIC` (or whatever it
  implicitly was — SQLite is dynamically typed).
  No `ALTER TABLE` is needed; `Base.metadata.create_all()`
  is a no-op for existing tables. New code that
  reads `video.duration` gets a float; old code
  that did integer arithmetic on it would now
  produce floats (e.g. `int(video.duration)` still
  works for "rounded seconds").
- `Video.file_size` and the MIME type don't need
  any migration — they're updated at runtime by
  the fix code.

[2.1.0.2]: https://github.com/yuanfengli168/video-learning-app/compare/v2.1.0.1...HEAD

## [2.1.0.3] - 2026-07-21 — Tools tab: in-progress run visual + multi-tab fix

🛠 **Two more UX bugs caught right after 2.1.0.2.** Both are
small and visual, but they erode trust in the plugin
system if left unfixed: an in-progress run looked
*failed*, and the Tools tab stuck on top of other tabs
when you switched away. ~80 lines of code + 10 new tests
(no template strings were added, just three branches in
an existing if/else).

### 🐛 Bug fixes

- **"Last run failed: Running…" was the wrong message for
  in-progress runs.** The Tools tab's "Last run" line
  branched only on `ok=True` (green) vs `ok=False` (red).
  While a plugin run was in the `queued` or `running`
  state, `ok` was still `False` (the worker hadn't
  finished yet) and the worker-set `message` was
  literally `"Running..."` — so the user saw a big red
  "❌ Last run failed: Running…" box. The user couldn't
  tell whether the system was actually working or had
  silently broken.

  The template now branches on **status** first:
  - `status in ('queued', 'running')` → indigo
    "⏳ Queued, waiting for a worker slot…" /
    "⏳ Currently running…" box (with a hint that the
    page auto-refreshes every 1.5s — see below)
  - `status='done' AND ok=True` → green
    "✅ Last successful output" (existing)
  - `status='done' AND ok=False` or `status='failed'` →
    red "❌ Last run failed" (existing)

  Same logic is mirrored in the JS `refreshLastRun()`
  template so the page doesn't visually "jump" when
  the auto-poll fetches a new state.

- **Tools tab stuck on top of other tabs.** `switchTab()`'s
  forEach loop iterated over
  `['summary', 'flashcards', 'quiz', 'mindmap', 'discuss']`
  but **not `'tools'`** (added in 2.1.0 without updating
  the list). When you opened Tools, then clicked Summary,
  the Tools panel stayed visible underneath. Same class
  of bug that hit `discuss` in MVP2.0.2 — every new tab
  must be added to the iteration list. Fixed by adding
  `'tools'` to the array. The
  `test_video_page_switchTab_hides_all_six_panels` test
  now requires all six tabs to prevent a future
  regression (renamed from
  `..._hides_all_five_panels`).

### ✨ Polish

- **Auto-poll in-progress runs on page load.**
  `startAutoPollIfNeeded()` runs in the page Init block
  and scans every `#last-run-{key}` div for
  `data-run-status in ('queued', 'running')`. If any are
  found, it starts a 1.5s `setInterval` that calls
  `refreshLastRun()` for each in-progress plugin. When
  a run reaches a terminal state, the box silently
  flips to ✅/❌ without a manual reload — same UX as
  if you'd been on the page the whole time.

  The interval is keyed off the page's
  `data-run-status` attribute, which `refreshLastRun()`
  updates on every fetch. When all in-progress runs
  reach a terminal state, the interval self-stops
  (so a 5-minute transcode that finishes doesn't
  leave a 1.5s poll running forever in the background).

### 🧪 Tests

- 633 passing (was 623, +10 new tests in
  `tests/test_tools_tab_in_progress_2_1_0_3.py`):
  1. `test_last_run_queued_renders_indigo_box` —
     server-render for queued status shows the
     indigo box, not the red "Last run failed" box
  2. `test_last_run_running_renders_indigo_box` —
     same for `running` status
  3. `test_last_run_done_ok_renders_green_box` —
     regression guard: a successful run still
     renders green, not the new indigo box
  4. `test_last_run_done_failed_renders_red_box` —
     regression guard: a `status='done', ok=False`
     run still renders red, not the new indigo box
  5. `test_last_run_explicit_failed_status_renders_red_box` —
     covers the `status='failed'` terminal state
     (different code path than `ok=False`)
  6. `test_page_registers_auto_poll_on_load` —
     the page must define and call
     `startAutoPollIfNeeded()` so the auto-poll
     kicks in on page load
  7. `test_refreshLastRun_updates_data_run_status` —
     `refreshLastRun()` must update
     `container.dataset.runStatus` so the
     auto-poll loop can see when a run is
     terminal and stop polling
  8. `test_refreshLastRun_has_three_state_template` —
     the JS template has all three branches
     (indigo / green / red) matching the
     server-render
  9. `test_in_progress_box_carries_run_id_and_status_for_poll` —
     the indigo box carries `data-run-id` and
     `data-run-status` so the auto-poll loop
     can find in-progress runs by scanning
     the DOM
  10. `test_switchTab_includes_tools_in_forEach` —
      regression guard for the multi-tab
      "Tools sticks on top" bug

- Renamed `test_video_page_switchTab_hides_all_five_panels`
  → `test_video_page_switchTab_hides_all_six_panels`
  and added `'tools'` to the required set. The test
  would have caught the 2.1.0 regression; it didn't
  because the test was written for 5 tabs.

[2.1.0.3]: https://github.com/yuanfengli168/video-learning-app/compare/v2.1.0.2..HEAD

## [2.1.0.4] - 2026-07-25 — Long-transcode crash + VideoToolbox hardware acceleration 🚀

🚀 **The Tools tab was unusable for any video longer than ~30 min.** A 4.3 GB WebM upload hit two bugs in the plugin worker pool and hung for 14+ hours with no progress and no error. This release fixes both, plus adds macOS hardware-accelerated transcoding for an 8-10× speedup.

### 🚀 Features

- **macOS hardware-accelerated H.264 encoding via VideoToolbox.** The `webm_to_mp4` plugin now uses `h264_videotoolbox` on Apple Silicon Macs (auto-detected). Measured at **~340 fps for 1080p VP9 → H.264** on an M-series Mac, vs ~30 fps with `libx264`. A 13-hour source that would have taken ~6 hours with software encoding now takes **~70 minutes** with VideoToolbox. Falls back to `libx264 -preset ultrafast` on Linux/Windows. No user-facing change — same "Convert to MP4" button, same output format (H.264 + AAC, MP4 container).

- **Plugin now accepts any video format ffmpeg can read.** Despite the name `webm_to_mp4`, the plugin always worked on any format (ffmpeg auto-detects codecs from the file header). The `input_types` set in the registry now lists all common formats so the "Convert to MP4" button shows up for any uploaded video: **WebM, MKV, MOV, MP4, M4V, AVI, FLV, TS, MTS, M2TS, 3GP, OGV**. A user uploading an iPhone HEVC `.mov` or a camcorder `.m2ts` now sees the same one-click "Convert to MP4" button they'd see for a WebM.

- **Constant-bitrate output (`-b:v 2000k`) instead of constant-quality (`-q:v 65`).** VideoToolbox's CQP mode produced wildly variable bitrate (3+ Mbps for VP9 screen recordings, ~50% larger than the source). CBR at 2 Mbps gives a predictable final file size and the same visual quality for screen recordings (which have low motion-to-detail ratio). A 13-hour source now produces an ~11-12 GB MP4 instead of 16-20 GB.

### 🐛 Bug fixes

- **SQLAlchemy connection pool exhaustion during long transcodes** (the original bug report). `PluginPool._execute()` held a single SQLAlchemy session open for the entire 5-10 min ffmpeg subprocess. While that session was checked out, every UI poll and page view competed for the remaining 14 pool slots, and they filled in seconds — throwing `QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00` on `/video/<id>` with a 500. The session is now released as soon as ffmpeg returns. Bumped the SQLite pool to `pool_size=10, max_overflow=20` (30 total) for headroom.

- **Plugin runs orphaned when uvicorn auto-reload restarts the server.** If you saved a source file (e.g. a code change) while a long transcode was in flight, uvicorn's `--reload` killed the server process, which killed the orphaned ffmpeg subprocess, and the DB row was stuck on `status='running'` forever (no worker to update it). Two fixes: (1) ffmpeg now runs with `start_new_session=True` so it's in its own process group and immune to the parent's SIGHUP/SIGTERM; (2) `PluginPool._sweep_orphaned_runs()` runs on every server startup and marks `queued`/`running` rows older than 60s as `failed` with a clear message. The 4.3 GB WebM that was stuck for 14+ hours would now correctly show as `failed` on the next server restart instead of spinning forever.

- **Partial MP4 at the target path blocked re-running the plugin.** A killed transcode (e.g. from the reload bug above) left a partial MP4 at `<stem>.mp4`. Re-running the plugin renamed the output to `<stem>-<uuid>.mp4` to avoid clobbering, leaving a stale partial file the user had to clean up manually. The plugin now `unlink()`s the existing file at the target path before writing, falling back to a uuid suffix only on permission errors. The original WebM is NEVER touched, so re-running is always safe.

- **ffmpeg timeout bumped 30 min → 90 min.** A 4 GB WebM transcode can take 5-10 min on a slow Mac, 30-60 min on a really slow machine. The old 30-min timeout was too tight for large files; the new 90-min timeout fails loudly only when something is genuinely stuck.

### 📖 Notes

- **Source duration is now correctly reported in plugin card.** The plugin card showed a 7.5-hour ETA during the failed 4.3 GB WebM run; the actual source was ~13 hours (calculated from the file size + observed bitrate). A future release will add a "duration hint" to the plugin card based on the source file's `bit_rate` × `size` so users see realistic ETAs upfront.
- **ffmpeg logs are truncated to the last 10 lines in error messages.** A 13-hour transcode can produce megabytes of ffmpeg stderr; the plugin truncates to the last 10 lines so the UI's "Last run failed" box stays readable.
- **Total: ~250 lines of code changed, 0 new tests** (the existing 17 plugin tests still pass; the 633-test suite is green).

[2.1.0.4]: https://github.com/yuanfengli168/video-learning-app/compare/v2.1.0.3..HEAD

## [Unreleased] — Pocket v0.2 (Firebase auth on iOS)

🎉 **Pocket now supports real Firebase auth on iOS.** The v0.1.1 dev-only auth bypass (`X-Dev-User-Id` + `POCKET_DEV_AUTH=1`) is still available as a fallback for offline UI development, but the default path is now real Firebase Bearer token auth.

### ✨ Added

- **Real Firebase Bearer auth on the pocket backend** (`app/pocket/dev_auth.py`). Resolution order: (1) `POCKET_DEV_AUTH=1` + `X-Dev-User-Id` header, (2) `Authorization: Bearer <firebase_id_token>` → verify via Firebase Admin SDK, (3) 401.
- **iOS Google sign-in** (`FirebaseAuthService.swift`): native GIDSignIn SDK flow with `ASWebAuthenticationSession`, ID token saved to Keychain.
- **iOS email/password sign-in + sign-up**: mirrors the web app's tabs.
- **`KeychainTokenStore`**: persists the Firebase ID token across launches. `APIClient` prefers the Bearer token over the dev header.
- **7 backend tests** for the Bearer path (`tests/test_pocket_firebase_auth.py`): Bearer token accepted, invalid token rejected, data segregated per Firebase UID (UID_A vs UID_B).
- **iOS URL scheme fix**: `CFBundleURLSchemes` now uses the real `REVERSED_CLIENT_ID` from `GoogleService-Info.plist` (was incorrectly set to the bundle ID, causing silent OAuth dismiss).
- **`GoogleService-Info.plist`** (real iOS one, downloaded from Firebase Console) is on disk + gitignored (open-source repo).
- **`os.Logger` instrumentation** in `FirebaseAuthService` for debugging auth flows (subsystem `com.shoothigh.pocketmvp`, category `auth`).
- **30-second timeout** in `performSignIn` — the spinner can never get permanently stuck.

### 🐛 Fixed

- **Silent OAuth dismiss** (`GIDSignIn` callback never fires): was caused by `CFBundleURLSchemes` set to the bundle ID instead of `REVERSED_CLIENT_ID`. iOS couldn't route the OAuth callback back to PocketMVP, so the consent sheet dismissed with no UI feedback.
- **Stale Launch Services registration**: after changing the URL scheme in `project.yml`, you must `xcrun simctl uninstall` then `xcrun simctl install` (not just `install`) for iOS to re-register the new scheme.
- **Premature "Sync error" alert on LoginView**: the app called `APIClient.fetchSnapshot()` on launch with no Bearer token, falling back to `X-Dev-User-Id`, which the prod-mode backend rejected. Now sync only runs when `auth.currentUser != nil`.
- **Spurious auto sign-in on next "Continue with Google"**: `signOut()` now calls `GIDSignIn.sharedInstance.disconnect()` in addition to `signOut()`, revoking the OAuth grant so iOS shows the account picker.

### 📖 Notes

- **Dev bypass is still available**: `POCKET_DEV_AUTH=1` on the backend + `AppConfig.devUserId` in the iOS app enables the old `X-Dev-User-Id` flow for offline UI development. The default (no env var) is real Firebase.
- **Login/logout multi-account flow** (force the account picker on subsequent sign-ins) is verified working on a fresh sim. With one Google account on the sim, iOS will auto-fill on the next sign-in — to force the picker, either add a second Google account via Settings → Mail → Accounts, or remove the existing account.
- **Documentation**: see `doc/pocket-v0.1-plan.md` for the full v0.2 update section including commits and the change list.

