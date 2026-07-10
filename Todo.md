# Todo — Future Ideas

> **This is a wishlist, not a plan.** Items here are brainstormed / discussed / requested but **not yet scheduled for implementation**. Each item has:
> - A short description
> - Why it would be valuable
> - A rough effort estimate
> - What it would block / depend on
>
> To pick something up, move it from this file into a real issue / branch / MVP2 milestone plan.

---

## 1. Expose the backend as a reusable API for other AIs (MCP / OpenAPI)

**Idea:** Make the transcript + mindmap + flashcards + chat backend consumable by external AI clients (Claude Desktop, Cursor, other agents), not just our own Jinja2 UI.

**Two ways to expose it:**

### Option A: Model Context Protocol (MCP) server
- MCP is Anthropic's standard for letting AI agents call tools from local processes
- We'd publish tools like:
  - `transcribe_video(video_id, language="zh")` → returns segments
  - `get_mindmap(video_id)` → returns the markdown tree
  - `search_transcript(video_id, query)` → returns matching segments + timestamps
  - `ask_about_video(video_id, question)` → wraps the existing chat endpoint
  - `list_courses()` / `list_videos(course_id)` → navigation
- Implementation: a new `app/mcp_server.py` using the official `mcp` Python SDK; runs as a sidecar process on a different port (e.g. 8001) alongside uvicorn
- **Why valuable:** Lets you (or anyone) pipe the app's data into Claude/Cursor conversations. E.g. "Claude, read my RAG video's mindmap and quiz me on the weak areas" — all from the agent's native UI
- **Effort:** ~3-5 days (define the tool schema, write the MCP server, add tests, document)

### Option B: OpenAPI / REST API for third-party developers
- We already have a FastAPI backend with ~15 endpoints — just publish the OpenAPI schema at `/openapi.json` (FastAPI does this for free)
- Add an API key auth flow (`X-API-Key` header, separate from Firebase session cookies)
- Add a rate limiter (per-key) to prevent abuse
- Document the API at `/docs` (FastAPI's built-in Swagger UI) and/or `docs/api.md`
- **Why valuable:** Lets people build their own frontends (mobile app, browser extension, CLI tool) on top of the backend. Opens up the project to non-Python developers
- **Effort:** ~1-2 days (auth + rate limiting + docs are the bulk)

### Option C: Both (MCP for AI agents, OpenAPI for human developers)
- Best of both worlds. The two are not in conflict — MCP tools can internally call the same Python functions as the REST handlers
- **Effort:** ~5-7 days (start with A, layer B on top)

**Dependencies:**
- MVP1 must be stable (it is — we just shipped v1.0.0)
- The endpoints need to be **idempotent** (they mostly are, but `POST /api/generate/{video_id}` is not — re-running it regenerates materials)
- For Option A specifically, the user running the MCP server must trust the local app with their data (same trust level as the web UI today, so no new concerns)

**Status:** brainstorming only. Not started.

---

## 2. Progress bar + ETA for long-running jobs

**Current state:** Both `/api/videos/{id}/transcribe` and `/api/generate/{id}` are **synchronous and blocking** — the request hangs for 1-10 minutes while Whisper/Ollama do their thing, and the UI shows just a spinner. For a 1-hour Chinese video, the user has no idea if it's 10% done or 99% done.

**Why valuable:** A 10-minute black box is the #1 source of "is it broken?" support tickets. With a progress bar + ETA, the user knows:
- The job is alive (not just hanging)
- Roughly how much longer to wait
- Whether to switch to a smaller Whisper model and re-run

**Approach (full plan, not implementing now):**

### Phase 1: Make the backend async + push progress (MVP2, ~2-3 days)
- Move transcribe/generate off the request thread and into a background task queue
- Best fit: **Celery + Redis** (the standard Python pattern, already mentioned in `doc/design.md` MVP2 §2)
- Alternative for single-user MVP1+: **BackgroundTasks + polling endpoint** (FastAPI has `BackgroundTasks` built in — no Celery needed for one user)
- Store progress in the `videos` table (new columns: `transcribe_status`, `transcribe_progress_pct`, `transcribe_started_at`)
- New endpoint: `GET /api/videos/{id}/transcribe/status` returns `{status, progress_pct, eta_seconds, message}`

### Phase 2: Wire up the UI (MVP2, ~1 day)
- Replace the spinner with a real progress bar (Tailwind has `<progress>` styling, or use a custom div)
- Poll the status endpoint every 2 seconds while the job is running
- Display ETA as "About 3 minutes remaining" (compute from `progress_pct` and elapsed time)
- Show a "Run in background — close this tab?" link so the user can navigate away

### Phase 3: Whisper-level progress (MVP2, ~0.5 day)
- Faster-Whisper already supports `log_progress=True` which prints segment-level progress to stdout
- Pipe that through to the progress store so the user sees "Transcribed 540/1832 segments"

**Effort:** ~3-5 days total. Defer until MVP2.

---

## 3. Language selection (Simplified vs Traditional Chinese, etc.)

**Current state:** Whisper auto-detects the language. For a Cantonese / Taiwanese Mandarin video, it may output 繁体中文 (Traditional Chinese). The user has no way to override this.

**Why valuable:** Two real scenarios:
- User has a Traditional Chinese video but wants the transcript in Simplified (or vice versa) — for searching, copy-paste, downstream tools
- User has a code-switching video (Mandarin + English) and wants to lock the transcription to "Mandarin only" to avoid Whisper drifting into English

**Approach (full plan, not implementing now):**

### Whisper-side: pass `language=` and `task=` explicitly
- `app/services/transcription.py` already calls `model.transcribe(...)` — add a `language` parameter
- Whisper language codes: `zh` (auto), `zh-Hans` (Simplified), `zh-Hant` (Traditional), `en`, `ja`, `ko`, etc.
- Map UI choice → Whisper language code

### UI: add a dropdown next to the model selector
- New dropdown: "Output language" with options:
  - `Auto-detect` (current behavior)
  - `中文 (简体)` → Whisper `zh` + post-process to Simplified (most Chinese output is already Simplified)
  - `中文 (繁體)` → Whisper `zh` + post-process to Traditional (via OpenCC `s2t`)
  - `English` → Whisper `en`
  - `日本語` → Whisper `ja`
  - `한국어` → Whisper `ko`
- The dropdown shows the **detected** language after first transcription (so the user can see what Whisper picked)
- Pass the choice down to the API via the existing `model_name` query param (rename to `transcribe_options` or add a new `language` query param)

### OpenCC for Simplified ↔ Traditional conversion
- `pip install opencc-python-reimplemented` (~50KB, pure Python, no native deps)
- One-line conversion: `OpenCC('s2t').convert(text)` or `OpenCC('t2s').convert(text)`
- This is a **post-processing step** after Whisper, applied to each segment's `text` field

### Estimated effort
- Service layer (Whisper lang param + OpenCC): ~0.5 day
- UI dropdown + state management: ~0.5 day
- Tests: ~0.5 day
- **Total: ~1.5 days, could land as part of MVP1.x or early MVP2**

**Dependencies:** None. Could land any time.

---

## How to use this file

- **Brainstorming session?** Add a new item to this file. Don't implement yet.
- **Ready to schedule?** Move the item to a real issue / branch / milestone plan. Leave a one-line note here: `> Moved to issue #42 (MVP2.1)` and link the issue.
- **Done?** Delete the item entirely. The CHANGELOG captures the user-facing record.

This file is intentionally **flat and unstructured** — it's a parking lot, not a roadmap. The roadmap lives in `doc/MVP1.0-successfullyFinished.md` §10 (Recommended next steps) and `doc/design.md` §2 (MVP2).

## 4. One-shot re-run of yesterday's 4 LLM-failed videos

**Idea:** On 2026-07-09, 4 of 30 bulk-uploaded videos (#6, #34, #35, #36) failed
in the LLM step with `Could not extract valid JSON from LLM response (len=0)`.
The transcripts are intact; only the materials generation step needs to be
re-run. Build a small CLI script (or a one-off DB fix) that re-runs
`_run_generate_job` for any video with `g_status='failed'` and `g_error LIKE
'%len=0%'`.

**Why valuable:**
- The 4 videos have working transcripts but no mindmap/quiz/flashcards
- User wants to discuss "script vs Regenerate button vs skip" tomorrow, but
  having the data ready means the discussion is about UX not data recovery
- Future-proofs: same script can be reused if Ollama flakes again

**Approach:**
- A small `scripts/retry_failed_generate.py` that:
  1. Finds all `videos` with `json_extract(last_generate_job, '$.error')` matching
     the empty-response pattern
  2. Re-runs `_run_generate_job(video_id, model_name='base')` synchronously
  3. Reports success/failure per video
- Dry-run flag (`--dry-run`) so the user can preview what would be retried
- Logs each retried video's `video_id` so we can verify in the UI afterward

**Effort:** ~30 min including tests for the helper function
(`find_failed_generate_videos(db) -> list[str]`).

**Dependencies:** None.

**Status:** Not started. Tomorrow's session.

---

## 5. "Retry all failed" button in the section view

**Idea:** On the course page (section view), add a button next to each section
header that says "Retry all failed". It hits a new bulk endpoint that re-queues
the LLM step for every video in that section that has `status='error'` and
`g_status='failed'`. The user gets one toast: "Retried 4 videos (2 succeeded,
2 still failing)".

**Why valuable:** Today, the only way to retry a failed generation is to click
"Generate" on each individual video page. With 4 failed out of 30, that's 4
clicks + 4 page loads. One button: 1 click. As the library grows, this matters
more (imagine 50 videos, 10 failed).

**Approach:**
- New endpoint: `POST /api/courses/{course_id}/sections/{section_id}/retry-failed`
- Body: none. Iterates `section.videos`, finds those with `g_status='failed'`,
  kicks off `_run_generate_job` as a `BackgroundTask` per video
- Returns: `{retried: int, succeeded: int, failed: int, video_ids: [...]}`
- UI: button in section header, disabled if no failed videos, with a spinner
  while in progress
- Same endpoint can be reused for the dashboard "Retry all failed across all
  sections" view in the future

**Effort:** ~1 day (endpoint + tests + UI button + spinner state + toast)

**Dependencies:** Depends on the per-video retry logic from #6. Should be done
after #6.

**Status:** Not started. Tomorrow's session.

---

## 6. "Retry this video" button on the video page

**Idea:** On the video player page, when `status='error'`, show a "Retry
generation" button instead of (or next to) the current error message. Clicking
it calls `POST /api/videos/{id}/generate` which kicks off `_run_generate_job`
as a BackgroundTask. UI polls `/status` and shows a progress bar.

**Why valuable:** Today, after a video fails, the user has to:
1. Notice the error
2. Navigate to the video page
3. Click "Generate" (which re-runs the same LLM call that just failed)
4. Wait for it to finish
5. Hope this time it works

A dedicated "Retry" button with a fresh status poll makes the recovery
flow obvious. The backend logic already exists (`_run_generate_job`); we just
need a thin endpoint + UI button.

**Approach:**
- New endpoint: `POST /api/videos/{id}/generate` (or rename the existing
  `POST /{video_id}/transcribe` to be more general)
- Idempotent: if the video is already in `generating` state, return 409
- UI: on the video page, when `status='error'`, show a yellow "Retry
  generation" button. On click, optimistic UI: status badge flips to
  "generating", start polling `/status`

**Effort:** ~0.5 day (small endpoint + tests + button + status poll)

**Dependencies:** None. Could ship before #5.

**Status:** Not started. Tomorrow's session.

---

## 7. Export transcript as .md

**Idea:** On the video page, add a "Download transcript" dropdown with three
options: Markdown (.md), JSON (.json), Plain text (.txt). Each hits a new
endpoint that returns the transcript in the requested format as a file
attachment.

**Why valuable:** Users have asked for export in different formats. Today the
only way to get the transcript is to read it from the UI. With an export:
- .md → paste into Obsidian, Notion, blog posts
- .json → programmatic use (e.g. feed into another AI tool)
- .txt → grep-friendly, simple paste into Word

**Why .md for the first format:** It matches the existing `summary` and
`mindmap` formats, so users have a consistent experience.

**Approach (just the .md part of this):**
- New endpoint: `GET /api/videos/{id}/transcript/export?format=md`
- Response: `Content-Disposition: attachment; filename="{title}.md"` + body
- Body format:
  ```markdown
  # {video.title}

  **Duration:** {duration}s | **Language:** {language} | **Exported:** {date}

  ## Transcript

  [00:00] 第一段文字
  [00:03] 第二段文字
  ...
  ```
- Tests: format matches expected output, content-type, filename

**Effort:** ~2-3 hours

**Dependencies:** None. Independent of #8 and #9.

**Status:** Not started. Tomorrow's session.

---

## 8. Export transcript as .json

**Idea:** Same as #7 but JSON. Returns the raw `Asset` content (already
stored as JSON in the DB) with proper `Content-Disposition` so the browser
downloads it.

**Why valuable:** Programmatic use — feed into another tool, build a
custom analysis, etc.

**Approach:**
- Same endpoint as #7 with `?format=json`
- Body: the exact JSON from `assets.content` for `asset_type='transcript'`
- Content-Type: `application/json; charset=utf-8`
- Filename: `{title}.json`
- Tests: matches the stored asset, content-type correct, JSON parses

**Effort:** ~1 hour (literally just returns the existing asset)

**Dependencies:** #7 (same endpoint, just another format value)

**Status:** Not started. Tomorrow's session.

---

## 9. Export transcript as .txt

**Idea:** Same as #7 and #8 but plain text. One segment per line, format:
`[00:00:15] 文字内容`. No markdown, no JSON, just text. Useful for grep, Word
paste, reading on a phone.

**Why valuable:** Lowest-friction export. No formatting to deal with. Easy
to share.

**Approach:**
- Same endpoint with `?format=txt`
- Body: each segment rendered as `[HH:MM:SS] {text}\n` (note: HH, not MM —
  durations over 1 hour are common)
- Content-Type: `text/plain; charset=utf-8`
- Filename: `{title}.txt`
- Tests: format matches, timestamps padded correctly (including >1h
  durations)

**Effort:** ~1 hour

**Dependencies:** #7 (same endpoint)

**Status:** Not started. Tomorrow's session.

---

