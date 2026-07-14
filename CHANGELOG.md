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
- **"ready · 9:08" timing badge** (`ae4df7d`) — every video's status badge on the course page now shows how long it took from upload to ready, e.g. `ready · 9:08` or `ready · 2:05:33` for > 1 hour. Stamped by the transcribe + generate workers, formatted via a new `format_duration` Jinja filter.
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
