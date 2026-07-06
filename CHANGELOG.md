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

[1.0.0]: https://github.com/yuanfengli168/video-learning-app/releases/tag/v1.0.0
