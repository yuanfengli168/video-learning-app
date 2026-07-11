# MVP3.0 — Status

> **Status:** Planning. No code yet.
> **Tagline (from manual todo [jul11] #2):** "MVP3.0: optimize performance and prompt engineering etc, with Jira creation (MCP)"
> **Status doc style:** Short Kanban table at the top (one row per item, easy to
> scan). Detailed sections below for items that need depth. Mirrors the format
> of `doc/MVP2.0-Status.md` but lighter — this is the *what* and *when*; the
> *how* lives in `doc/design.md` and `doc/BlockersOrChallengers.md`.

---

## Pillar overview

| # | Pillar | Theme |
|---|--------|-------|
| 1 | **Performance** | Make 100-video batches usable on a Mac. Local + cloud options. |
| 2 | **Reliability** | Bugs the user is hitting on the current MVP2.0 build. |
| 3 | **UX** | Things the user notices but MVP2.0 didn't get to. |
| 4 | **Prompts & content** | Quality of the LLM-generated materials. |
| 5 | **Scale & business** | Cloud paid tier, soft-delete/trash, MCP integrations. |

---

## Status table (Kanban)

> Sort order: Pillar → Priority. **P0** = blocks user today. **P1** = next
> sprint. **P2** = nice-to-have. **P3** = paid tier / future.

| # | Item | Pillar | Priority | Status | Notes / Source |
|---|------|--------|----------|--------|----------------|
| 1 | Raise `MAX_FILE_SIZE` from 2 GB → **10 GB** | Reliability | **P0** | Not started | `doc/manualTodo.txt` [jul11] #3. `app/routers/videos.py:36`. Investigated in `doc/BlockersOrChallengers.md` §1. |
| 2 | Whisper backend swap: `faster-whisper` → **`mlx-whisper`** (M-series native) | Performance | **P0** | Not started | 3-4x speedup on Apple Silicon. Investigated in `doc/BlockersOrChallengers.md` §1. Phase 1: also try `distil-large-v3` for 6-7x. |
| 3 | Cloud Whisper API (paid tier) — opt-in per-video | Scale | **P2** | Not started | $0.006-0.012/min. 1-hour audio in ~1-2 min. **Only way to hit 1-min/16-hr target** (see `BlockersOrChallengers.md` §1). |
| 4 | Background worker pool + status polling for batch uploads | Performance | **P1** | Not started | Lets user queue 100 videos and walk away. `BackgroundTasks` is single-process today. |
| 5 | Soft-delete / trash / restore (30-day TTL) | Reliability | **P1** | Not started | `doc/manualTodo.txt` [jul10] #8. Currently we hard-delete on click. |
| 6 | Note section (markdown, preview, save to DB) | UX | **P1** | Not started | `doc/manualTodo.txt` [jul11] #6. Could be Notion embed + DB-backed, or full markdown editor. |
| 7 | Video player: manual scroll-to-end on 2-hour videos | UX | **P1** | Not started | `doc/manualTodo.txt` [jul11] #4. Current player struggles with long files. Plyr / video.js swap candidate. |
| 8 | Show "ready in 9:08" timing next to each video's status badge | UX | **P2** | Not started | `doc/manualTodo.txt` [jul11] #8. Store `transcribed_at` + `generated_at`, render `ready in N:SS` on section page. |
| 9 | Language consistency in generated materials | Content | **P2** | Not started | `doc/manualTodo.txt` [jul10] #7. Today: sometimes Chinese, sometimes English. Make it deterministic (always match video language). |
| 10 | OCR of video frames for the Discuss tab | Content | **P3** | Not started | `doc/manualTodo.txt` [jul10] #6. Currently only Whisper transcript + materials. |
| 11 | Data flow chart of all functions in the app | Docs | **P2** | Not started | `doc/manualTodo.txt` [jul11] #7. Generate a `doc/architecture.md` with Mermaid. |
| 12 | Jira creation via MCP (paid tier) | Scale | **P3** | Not started | `doc/manualTodo.txt` [jul11] #2. Auto-create Jira tickets from app errors / user feedback. |
| 13 | Migrate SQLite → Alembic-managed schema | Reliability | **P2** | Not started | Today: hand-rolled `CREATE TABLE IF NOT EXISTS` + ad-hoc migrations. Alembic is the right tool once we have a paid tier (zero-downtime migrations). |
| 14 | i18n (UI strings) | UX | **P3** | Not started | Hardcoded English today. Once we have paying users in other regions, this matters. |

---

## In-depth sections

> Only items that need more than one paragraph of context get a section here.
> The table above is the source of truth for *what's in MVP3.0*.

### 1. Raise upload cap to 10 GB

**Why P0:** the user is actively blocked on uploading lecture recordings > 2 GB.

**Where:** `app/routers/videos.py:36` — `MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024`.
Change to `10 * 1024 ** 3`. Update the error message in the same file (the
"`f"Max: {MAX_FILE_SIZE // (1024**3)} GB"`" interpolation already handles any
number, no message change needed).

**Test:** single upload of a 10 GB file succeeds, single upload of a 10.5 GB
file returns 413 with the new cap shown in the error.

**Risk:** uvicorn/Starlette default request body limit is unbounded, so no
server-side change. But the **browser** may time out on a 10 GB upload over
slow networks — that's a user-experience thing, not a code thing.

**Estimate:** ~10 minutes (1-line constant, 2 tests).

### 2. Whisper backend: `faster-whisper` → `mlx-whisper`

**Why P0:** 100 × 10-min videos take ~3.2 hours of pure transcription today
with `base` model. Target: < 30 min. MLX is 3-4x faster on M-series.

**Where:** `app/services/transcription.py` — currently calls
`WhisperModel(model_size, device="auto", compute_type="int8")`. Needs a backend
selector + lazy import (mlx-whisper only on Apple Silicon).

**Phase 1 (this week):** try `distil-large-v3` first. 6-7x speedup, drop-in
replacement, no code change other than the model name.

**Phase 2 (next week):** swap to `mlx-whisper` for M-series, keep
`faster-whisper` for non-Apple-Silicon (Intel Macs, Linux servers).

**Test:** same `test_transcription.py` suite, but parameterised over backend.
Add a "this machine is M-series" fixture so CI doesn't try to import
`mlx_whisper` on Linux.

**Risk:** model weight format is different (mlx uses its own `.npy` format). Need
a one-time conversion step. Document in `scripts/setup.sh`.

**Estimate:** Phase 1 = 30 min. Phase 2 = 1 day.

### 4. Background worker pool

**Why P1:** current `BackgroundTasks` runs one video at a time per FastAPI
worker. Uploading 100 videos queues them all in series. With a worker pool of
N=4, throughput is 4x.

**Where:** new `app/workers/` module. Use `concurrent.futures.ThreadPoolExecutor`
for the simple version (Whisper is GIL-released via CTranslate2, so threads
work). For MVP3.0 paid tier, swap to Celery + Redis.

**Status endpoint:** `GET /api/videos/{id}/transcribe-status` already exists.
Just add a per-section aggregated view: `GET /api/sections/{id}/progress` that
returns `{ready: 87, processing: 4, error: 2, queued: 7}` so the section
page can show a live progress bar.

**Estimate:** 1-2 days.

### 5. Soft-delete / trash / restore

**Why P1:** `doc/manualTodo.txt` [jul10] #8 — user wants a 30-day trash before
permanent deletion.

**Where:**
- Add `deleted_at` column to `videos` / `sections` / `courses` (nullable, indexed)
- Change all `DELETE` endpoints to `UPDATE ... SET deleted_at = NOW()`
- Add `GET /api/trash` and `POST /api/trash/{id}/restore` and `DELETE /api/trash/{id}/permanent`
- Add a "Trash" sidebar entry with a 30-day countdown
- Nightly job: `DELETE FROM videos WHERE deleted_at < NOW() - INTERVAL 30 DAY`
  (move file to a `uploads/trash/<date>/` before unlink, for one more layer of safety)

**Estimate:** 2-3 days. See `doc/design.md` for the soft-delete section
already drafted.

### 6. Note section (markdown)

**Why P1:** `doc/manualTodo.txt` [jul11] #6 — user wants to take notes on each
video, save them, and have them show up later.

**Two options:**
- **A. Lightweight:** a `<textarea>` per video, saves plain markdown to a new
  `video_notes` table. Render with `marked.js`. ~1 day.
- **B. Notion-embed:** Notion's API has an embed mode that lets you mount a
  Notion page on your site and save to Notion. ~2 days + Notion account setup.
  Saves the user from losing notes if they leave.

**Recommendation:** start with A, ship it, then offer B as opt-in.

**Estimate:** A = 1 day, B = 2 days.

### 7. Long-video player (2-hour seek)

**Why P1:** `doc/manualTodo.txt` [jul11] #4 — current `<video>` element
struggles with manual seek on 2-hour files (jumps to wrong position, no
thumbnail preview).

**Two options:**
- **A. Switch to `plyr.js` or `video.js`** — both have a proper seek bar with
  buffered-range highlighting. ~4 hours.
- **B. Build a custom timeline** with thumbnail previews at each minute mark
  (extract 1 frame per minute, store as `.jpg`, lazy-load). ~2 days.

**Recommendation:** A first, B only if A doesn't feel good enough.

**Estimate:** A = 4 hours, B = 2 days.

### 9. Language consistency

**Why P2:** `doc/manualTodo.txt` [jul10] #7. The current prompt doesn't pin
the output language, so the LLM sometimes picks Chinese, sometimes English,
sometimes mixes both.

**Where:** `app/services/llm.py` — the system prompt + few-shot examples need
to include `"Always respond in {{detected_language}}"`. Detect language from
the first 5 transcript segments (Whisper also returns a `language` probability
in its `detect_language` method).

**Estimate:** 4 hours + a regression test for "Chinese source → Chinese output".

### 11. Data flow chart

**Why P2:** `doc/manualTodo.txt` [jul11] #7. Generate a Mermaid diagram of the
full app (upload → transcribe → LLM → materials → UI) and embed it in
`doc/design.md` or a new `doc/architecture.md`.

**Estimate:** 2 hours.

### 13. Alembic migration

**Why P2:** today we hand-roll migrations in `app/database.py`. Works fine
while we have one schema version and one DB. Will break the day we ship a
paid tier with zero-downtime deploys.

**Where:** add `alembic/` to repo root, init the migrations dir, generate the
first migration from the current schema, port the existing hand-rolled
migrations as Alembic revisions.

**Estimate:** 1 day.

---

## What MVP3.0 is *not*

- A rewrite. MVP3.0 is **incremental** on top of MVP2.0, not a v3 architecture.
- A UI redesign. The Tailwind class strategy from MVP1.0 stays.
- A multi-user product. Single-user (one Firebase account per install) is the
  MVP1.0 contract and remains the MVP3.0 contract. Multi-tenant is post-MVP3.
- A mobile app. Web-only. PWA maybe, native no.

---

## Decision log (live)

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-07-11 | Whisper 1-min-for-16-hr is **paid-tier only** | Math says local-only floor is ~30 min even with best setup. Cloud API is the only path. |
| 2026-07-11 | Note section ships as **lightweight markdown first**, Notion embed later | Lower risk, faster to MVP3.0, can iterate. |
| 2026-07-11 | Soft-delete / trash is **P1 not P0** | User blocked on 2 GB cap, not on accidental delete. Cap fix first. |
| 2026-07-11 | M-series native (mlx-whisper) is **default backend**, faster-whisper is **fallback** | mlx is 3-4x faster, same weights, same accuracy. No reason not to use it on Apple Silicon. |

---

## Related docs

- [`doc/MVP2.0-Status.md`](MVP2.0-Status.md) — current state, what shipped
- [`doc/MVP2.0-first-designQuestions.md`](MVP2.0-first-designQuestions.md) —
  the design rationale that gets us to MVP3.0
- [`doc/BlockersOrChallengers.md`](BlockersOrChallengers.md) — bugs +
  investigations (the deep dives)
- [`doc/manualTodo.txt`](manualTodo.txt) — the user's raw feature requests
- [`doc/design.md`](design.md) — architectural design (long-form)
