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

**Stats (as of 2026-07-11):** 453 tests passing · 87% backend coverage · 29 new commits · 0 regressions

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
