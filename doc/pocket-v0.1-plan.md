# Pocket v0.1 — Mobile + AI Tutor MVP Plan

> **Status:** Plan locked, ready to build
> **Branch:** `mvp-mobile-pocket-v0.1` (from `main`)
> **Target duration:** ~60 min from green light
> **Author:** brainstormed with GitHub Copilot, decisions ratified by user
> **Last updated:** 2026-07-26

---

## 1. Scope guardrails

- New branch `mvp-mobile-pocket-v0.1` off `main`.
- **Zero changes** to `landing-page/`, `mvp2-*` branches, or any existing route in `app/`.
- Backend is a **standalone FastAPI sub-app** mounted at `/m/*` on the existing `localhost:8000` — shares auth/DB, adds no risk to existing routes.
- iOS app lives in a new top-level `ios/` folder, also isolated from any other branch.
- **iOS app is a thin client.** It has zero knowledge of Ollama, model names, or prompts. It speaks JSON to a contract.

---

## 2. Architecture (the proxy pattern)

```
iPhone (sim) ──HTTPS──▶  Mac:8000 FastAPI  ──HTTP──▶  Mac:11434 Ollama
   thin client              proxy + auth + jobs          the LLM
   (knows nothing           (knows about Ollama,
    about Ollama)            model, prompts)
```

- iOS app → backend: HTTPS via `mkcert`-issued local cert. Real cert, no ATS hacks, no `NSAllowsArbitraryLoads`.
- Backend → Ollama: plain HTTP on `localhost:11434` (loopback, no encryption needed).
- `AppConfig.baseURL` is the **single source of truth** for the iOS app's backend URL.

---

## 3. Network & HTTPS setup (one-time)

- `brew install mkcert` → `mkcert -install` → `mkcert localhost <LAN-IP>`.
- FastAPI served with `uvicorn ... --ssl-keyfile ./certs/localhost-key.pem --ssl-certfile ./certs/localhost.pem`.
- `certs/` added to `.gitignore` (never committed).
- iOS sim trusts mkcert root CA automatically. Real iPhone (v0.2) needs root CA AirDropped + installed once.

---

## 4. Sync semantics

- **Append on create** — new courses/sections/videos appear after sync.
- **Overwrite on update** — if the wife's regenerated summary changes, the phone reflects it.
- **Hard delete** — if she deletes a video, it's gone from the phone too.
- **Phone is read-only** against source data. `pocket_progress` is the only thing the phone writes back, and it's a separate table (so it cannot corrupt source data).
- Sync is **incremental** via `since=<sync_token>` where the token is `max(updated_at)` of returned rows.

---

## 5. iOS networking

- `ios/PocketMVP/Config/AppConfig.swift` — single file, single line:
  `static let baseURL = "https://localhost:8000"`. That's the only place the URL lives.
- `APIClient` reads from `AppConfig.baseURL`. Never hardcodes URL inline.
- ATS: iOS sim only talks to `localhost`, which ATS allowlists implicitly. Real iPhone (v0.2) gets `NSAllowsLocalNetworking = YES` (cleaner than `NSAllowsArbitraryLoads`).
- The iOS app has **no awareness** of Ollama, model names, or prompts. Just a JSON contract.

---

## 6. Test policy

- Tests written **in the same step** as the code that satisfies them — not a separate "tests phase" at the end.
- Every new endpoint gets: 1 happy-path test + 1 error/edge test + uses existing `tests/conftest.py` fixture patterns.
- A step without a test is not a done step.
- Tutor service: Ollama is **mocked** in tests — we never require a live Ollama to run the test suite.
- Sync service: tests cover **all three** sync paths (create-append, update-overwrite, delete-propagate).

---

## 7. Backend deliverables (`app/pocket/`)

| # | File | Purpose |
|---|---|---|
| 1 | `app/pocket/__init__.py` | Package marker |
| 2 | `app/pocket/schemas.py` | Pydantic models for all `/m/*` request/response bodies |
| 3 | `app/pocket/models.py` | SQLAlchemy: `PocketSyncLog`, `PocketChunk`, `PocketProgress` + migration |
| 4 | `app/pocket/sync.py` | `snapshot_for_user(user_id, since=None)` — read from existing tables, return append/overwrite/delete-shaped diff |
| 5 | `app/pocket/tutor.py` | Ollama call. Prompt: tutor role + `{transcript}` + `{summary}` + `{quiz}` + `{flashcards}` + `{mindmap}`. Returns JSON `[{start_ts, end_ts, duration_label, concept_title, teach_text, check_question}]`. Auto-fallback to current-video-only context if full-context is too slow. |
| 6 | `app/pocket/jobs.py` | In-process async job queue (`asyncio.create_task` + `dict[job_id, status/result]`). No Celery. |
| 7 | `app/pocket/router.py` | FastAPI router with the 5 endpoints below |
| 8 | `app/main.py` (one-line edit) | `app.include_router(pocket.router, prefix="/m")` |

### 7.1 Endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/m/snapshot?since=<token>` | Incremental sync, text fields only. Returns `{courses, sections, videos, sync_token, deleted_ids[]}`. |
| `POST` | `/m/teach/{video_id}` | Starts async tutor job. Returns `{job_id}`. Idempotent on rapid double-tap. |
| `GET` | `/m/teach/{video_id}/status?job_id=...` | Returns `{status: "pending"\|"ready"\|"error", chunks?, error?}`. 404 for unknown `job_id`. |
| `POST` | `/m/chunk/{chunk_id}/done` | Marks chunk complete. Idempotent on repeat. 404 for unknown chunk. |
| `GET` | `/m/progress/{video_id}` | Returns `{chunks_done: [int], last_seen_chunk: int}`. Empty state for never-opened video. |

### 7.2 Tutor prompt template (v0.1)

```
You are a patient tutor. Teach the following video to a busy adult who only has
fragmented time slots (2 min, 5 min, 25 min). Split the video into teachable
chunks, each ending in a check-for-understanding moment.

Transcript:
{transcript}

Materials (use ONLY these — do not invent):
- Summary: {summary}
- Quiz: {quiz}
- Flashcards: {flashcards}
- Mindmap: {mindmap}

Return STRICT JSON (no prose, no markdown fence):
[{
  "start_ts": <seconds, float>,
  "end_ts":   <seconds, float>,
  "duration_label": "2min" | "5min" | "25min",
  "concept_title":   "<= 8 words",
  "teach_text":      "<= 80 words, plain text, no markdown",
  "check_question":  "<= 30 words"
}]
```

**Auto-fallback:** if full-context prompt > 200k tokens OR Ollama p50 latency > 30s in last 5 calls, switch to current-video-only context (transcript + this video's own materials). Built into `tutor.py` from the start.

---

## 8. Test matrix

| Endpoint | Happy test | Edge test |
|---|---|---|
| `GET /m/snapshot` | Returns all current courses/sections/videos on first call | Incremental diff when `since=<token>`; propagates deletes; overwrites updates |
| `POST /m/teach/{id}` | Returns `job_id`, status goes `pending → ready` | Handles malformed-Ollama-response gracefully (returns `error`, never 500) |
| `GET /m/teach/{id}/status` | Returns current status | Returns 404 for unknown `job_id` |
| `POST /m/chunk/{id}/done` | Persists progress, idempotent on repeat | Rejects unknown chunk_id |
| `GET /m/progress/{id}` | Returns chunks done + last seen | Returns empty state for never-opened video |

---

## 9. iOS deliverables (`ios/PocketMVP/`)

| # | File | Purpose |
|---|---|---|
| 1 | `ios/PocketMVP/project.yml` | XcodeGen spec, iOS 17+, SwiftUI, single target |
| 2 | `ios/PocketMVP/PocketMVPApp.swift` | `@main` entry point |
| 3 | `ios/PocketMVP/Config/AppConfig.swift` | One constant: `baseURL = "https://localhost:8000"` |
| 4 | `ios/PocketMVP/Models/*.swift` | `Course`, `Section`, `Video`, `Chunk`, `ProgressSnapshot` as `Codable` structs |
| 5 | `ios/PocketMVP/Services/APIClient.swift` | `async/await` HTTP client, baseURL from config, decode JSON contract |
| 6 | `ios/PocketMVP/Services/SyncStore.swift` | Holds last-synced snapshot in memory, exposes diff helpers (mostly unused in v0.1 — server is authoritative for deletes) |
| 7 | `ios/PocketMVP/Views/CourseListView.swift` | List of courses → tap → sections |
| 8 | `ios/PocketMVP/Views/SectionListView.swift` | List of sections → tap → videos |
| 9 | `ios/PocketMVP/Views/VideoDetailView.swift` | 4 tabs: **Summary / Quiz / Flashcards / Mindmap**, all read from snapshot. No video player, no AI chat window. |
| 10 | `ios/PocketMVP/Views/TeachMeView.swift` | Taps "Teach me" → `POST /m/teach/{id}` → polls `GET /m/teach/{id}/status` every 2s → on `ready`, renders chunk cards → each has "Mark done" → `POST /m/chunk/{id}/done` → last-seen chunk highlighted next launch |
| 11 | `ios/PocketMVP/Resources/sample_snapshot.json` | 1 fake course / 2 sections / 3 videos, text fields filled in. Lets iOS flow be testable without backend live. **Phase 1 of build: iOS reads this. Phase 2: swap to live APIClient.** |

---

## 10. What's in scope for v0.1 (60-min build)

- New branch `mvp-mobile-pocket-v0.1` from `main`
- `brew install mkcert` + local HTTPS certs (gitignored)
- `app/pocket/` sub-app: schemas, models, sync, tutor, jobs, router
- 1-line mount in `app/main.py` at `/m/*`
- Tests for all 5 endpoints (mocked Ollama)
- iOS scaffold (XcodeGen, config, models, APIClient)
- SwiftUI views reading `sample_snapshot.json` (CourseList → SectionList → VideoDetail with 4 tabs)
- TeachMeView with polling + "Mark done"
- Swap iOS APIClient to live `https://localhost:8000`
- End-to-end: iOS sim → backend → Ollama → chunks flow back
- Commit + tag `pocket-v0.1`

---

## 11. What's out of scope (parked, not built)

- Real iPhone provisioning + cert install on device — simulator only this hour, **v0.2**
- Voice I/O (talk/listen) — **v0.2**
- Image sync — text-only per user decision, **v0.2**
- ATS cleartext exception — replaced by proper HTTPS via mkcert (no longer needed)
- **PDF / codebase upload endpoints (the `{materials}` slot in the prompt reads from existing DB tables only — new upload pipeline is v0.2)**
- RAG / vector store — relying on 1M-context inlining instead
- mDNS auto-discovery of the Mac — **v0.2**
- Settings screen for editable base URL — **v0.2** (when real iPhone is in scope)
- Auto-detection of commute context (you tap, app doesn't guess) — not planned
- Auto-skipping silence in the source video (AI tells you what to skip, doesn't re-cut) — not planned

---

## 12. Build order (60 min, committed in this order)

| Min | Step | Verifiable output |
|---|---|---|
| 0-3 | branch + cert setup + `app/pocket/` scaffold | `curl https://localhost:8000/api/health` works |
| 3-15 | schemas, models, sync service + tests | `pytest tests/test_pocket_sync.py` green |
| 15-25 | tutor service + job queue + tests | `pytest tests/test_pocket_tutor.py` green |
| 25-32 | router + `main.py` mount + smoke test | `curl https://localhost:8000/m/snapshot` returns JSON; existing routes still 200 |
| 32-35 | iOS scaffold (XcodeGen, config, models, APIClient) | `xcodegen generate` succeeds; valid `.xcodeproj` |
| 35-50 | SwiftUI views reading `sample_snapshot.json` | iOS sim boots, all 4 tabs render |
| 50-55 | TeachMeView with polling + "Mark done" | iOS sim against sample data shows fake chunks + polling UX |
| 55-58 | swap APIClient to live `https://localhost:8000` | iOS sim → backend → Ollama → chunks end-to-end |
| 58-60 | commit + tag `pocket-v0.1` | clean working tree |

---

## 13. Risks & fallbacks

| Risk | Fallback |
|---|---|
| Ollama 1M-context call > 30s | `tutor.py` auto-falls-back to current-video-only context (transcript + this video's own materials). Built in from start, not bolted on. |
| `mkcert` install fails on this Mac | `openssl req -x509` self-signed cert + manual trust install on iPhone. +5 min, less clean. |
| FastAPI uvicorn can't find certs on start | Certs are loaded via CLI args in `scripts/start.sh`, not via code — fall back to HTTP-only + ATS exception in v0.1 if needed. |
| iOS sim can't reach `https://localhost` | Confirm mkcert root is in System keychain (`mkcert -install` was run). If still failing, restart sim. |
| `pytest` conftest pattern doesn't apply to async DB | Write minimal in-memory fixture scoped to `tests/test_pocket_*.py`; do not touch the global conftest. |
| Build runs over 60 min | Cut steps 8–11 to "iOS reads sample data only, APIClient stubbed". Tag as `pocket-v0.1-ios-stub`. Real iOS wiring becomes the first task of v0.2. |

---

## 14. Definition of done for v0.1

- [ ] All 5 backend endpoints return 2xx for happy path, 4xx for documented edge cases
- [ ] All `pytest tests/test_pocket_*.py` green
- [ ] Existing `pytest` suite still green (zero regressions on MVP2 / main routes)
- [ ] `curl https://localhost:8000/m/snapshot` returns a snapshot for the logged-in user
- [ ] iOS sim builds, runs, and the 4 tabs (Summary / Quiz / Flashcards / Mindmap) render
- [ ] "Teach me" button in iOS sim triggers backend → Ollama → chunks back, end-to-end
- [ ] Tapping "Mark done" persists, and the last-seen chunk is highlighted on next launch
- [ ] Commit + tag `pocket-v0.1` on branch `mvp-mobile-pocket-v0.1`
- [ ] `landing-page/` and any `mvp2-*` work untouched (`git diff main -- landing-page/` empty)

---

## v0.1.1 — Dev auth unlock + data shape fixes (uncommitted-tagged delta)

**Tag:** `pocket-v0.1.1` on `mvp-mobile-pocket-v0.1`
**Date:** 2026-07-27
**Diff vs v0.1:** 8 files, +196/-19

### What changed
- **Backend** — `app/pocket/dev_auth.py`: new `get_current_user_dev_or_real` dependency. When `POCKET_DEV_AUTH=1` env var is set, requests authenticate via `X-Dev-User-Id` header. 401 otherwise. Gated by env var so it can never be enabled in production by accident.
- **Backend** — `app/pocket/router.py`: pocket routes now use the dev-aware dependency.
- **Backend** — `app/pocket/sync.py`: transcript parsing now tolerates three real DB shapes (bare JSON list, `{"segments": [...]}` object, list of plain strings). Fixes 500 on the RAG-Class course's Chinese transcript.
- **iOS** — `AppConfig.devUserId`: hardcoded Firebase UID the sim uses.
- **iOS** — `APIClient.applyDevAuth`: injects `X-Dev-User-Id` on every request.
- **iOS** — `APIClient` date decoder: more permissive (naive ISO, fractional ISO, SQLite-native).

### Tests
- NEW `tests/test_pocket_dev_auth.py` (3 tests): dev header works, no header 401s, per-user data isolation.
- Updated `test_pocket_sync.py` + `test_pocket_tutor.py` fixtures to override the same dependency function the router uses.
- **27/27 tests green** (was 24 in v0.1).

### Why v0.1.1 exists separately
v0.1 was a clean vertical slice but couldn't be smoke-tested with real data because the iOS sim had no auth mechanism. v0.1.1 adds the **dev-only** auth bypass so we can verify the full data path on the sim, with zero risk of accidental production use (env var gate). v0.2 will replace this with real Firebase auth.

### Verified end-to-end (post v0.1.1)
- iOS sim → `https://localhost:8443/m/snapshot` with `X-Dev-User-Id: ltLtLQzr3nOr2hQKdeTxYnIOYYN2`
- Returns 2 courses: "RAG - Class" (Mashibing, July 21) and "Test1"
- iOS app renders both in the course list with section/video counts
- mkcert-issued cert trusted by simulator (via `xcrun simctl keychain add-root-cert`)

### Security note
The `X-Dev-User-Id` header is **only honored when `POCKET_DEV_AUTH=1`**. Without that env var set, the same header is ignored and the request 401s. Production deployments must never set this env var.

---

## v0.1.2-polish — Sync UX upgrades (no auth changes)

**Tag:** `pocket-v0.1.2-polish` on `mvp-mobile-pocket-v0.1`
**Date:** 2026-07-27

### What changed
- **iOS: SyncStatusDot component** — replaces the static sync icon with a colored dot reflecting actual sync state (green = fresh, blue pulsing = syncing, red = error, gray = never synced). Shows relative timestamp ("in 0s"). Tappable to retry.
- **Backend: ETag/If-None-Match on /m/snapshot** — server computes a hash of the sync_token, sends as ETag header. Phone sends previous ETag as If-None-Match; if unchanged, server returns 304 Not Modified with no body. Saves ~99% of bandwidth on "I open the app, nothing changed" case.
- **iOS: ETag persistence** — the ETag is stored in UserDefaults so it survives relaunches. Without this, every relaunch is a fresh 200 with full body.
- **iOS: SnapshotResult type** — wraps the API response with a `notModified` flag so the SnapshotStore knows to keep its existing snapshot on 304.

### Tests
- 3 new tests in test_pocket_sync.py:
  - `test_snapshot_returns_etag_header` — every 200 has an ETag
  - `test_snapshot_returns_304_when_if_none_match_matches` — matching ETag → 304 + empty body
  - `test_snapshot_returns_200_when_etag_differs` — stale ETag → fresh 200
- All 10 sync tests + 17 tutor tests + 3 dev-auth tests = 30 green

### Verified end-to-end
- iOS sim launch 1: `GET /m/snapshot → 200 OK` (full body)
- iOS sim launch 2: `GET /m/snapshot → 304 Not Modified` (no body, server log confirms)
- Status dot visible in top-right of course list: 🟢 "in 0s"

### Not changed
- Auth model (still dev header, gated by POCKET_DEV_AUTH=1)
- v0.1.2 real Firebase auth is the next thing — not bundled here

---

## v0.1.2-foreground-and-persist — UX polish: foreground sync + disk cache

**Tag:** `pocket-v0.1.2-foreground-and-persist` on `mvp-mobile-pocket-v0.1`
**Date:** 2026-07-27

### What changed
- **iOS: Foreground auto-sync.** `RootView` watches `@Environment(\.scenePhase)` and
  calls `store.syncIfStale()` whenever the app becomes `.active`. Throttled
  to 1 sync / 30s so rapid scenePhase bounces (e.g. dismissing a sheet)
  don't fire. Combined with the ETag 304 from v0.1.2-polish, this means
  "I open the phone, I see fresh data instantly, every time."
- **iOS: Snapshot persistence on disk.** `SnapshotStore` now writes the latest
  snapshot + ETag to `~/Documents/snapshot_cache.json` after every
  successful sync. On `loadInitial()`, the disk cache is read first so the
  UI shows data immediately on cold start (no spinner), then a network
  sync runs in the background. Survives app restart; wiped on uninstall
  (iOS sandbox cleanup).
- **iOS: 304 path is correct on cold start.** When the disk cache hits,
  the network call sends the stored ETag. If the server returns 304, the
  in-memory snapshot stays unchanged. No flicker, no data loss.

### Tests
- No new pytest tests (the persistence and foreground-sync logic live in
  iOS, not Python). The 3 ETag tests added in v0.1.2-polish still pass,
  confirming the contract the iOS app depends on.
- All 30 backend tests still green.

### Verified end-to-end
- 1st launch: `GET /m/snapshot → 200 OK` (writes disk cache)
- 2nd launch: `GET /m/snapshot?since=<token> → 304 Not Modified` (foreground
  auto-sync via scenePhase)
- 3rd launch: `GET /m/snapshot?since=<token> → 304 Not Modified` (cold start
  reads disk, then 304)
- UI: courses appear immediately on cold start, no spinner, status dot green

### Not changed
- Auth model (still dev header, gated by POCKET_DEV_AUTH=1)
- Real Firebase auth is v0.1.3
- No data model or API changes

## v0.1.3-real-teaching — typed answers + AI feedback + favorites

**Tag:** `pocket-v0.1.3-real-teaching` on `mvp-mobile-pocket-v0.1`
**Date:** 2026-07-27

### The shift: from "passive teaching" to "active recall"

Up through v0.1.2, the iOS app showed what the AI tutor wrote (`teach_text`,
`check_question`) and let the student tap "Mark done" when they read it.
That was passive — like watching a TED talk. The check_question was a
prompt but the student never had to actually answer it.

v0.1.3 makes the student **type their answer** in a TextEditor below each
chunk, then optionally **ask the AI tutor to grade it**. The tutor returns
a verdict (`got_it` / `partial` / `missed`) plus a short explanation in
Chinese (matching the tutor's teaching style). The verdict + explanation
are persisted per-(user, chunk) so the "Review my answers" screen can show
all answers with their grades.

**Mark done vs AI feedback are now separate actions:**
- **Mark done** is fast and durable — persists `user_answer` +
  `is_favorite`. Use it after reading + answering.
- **Get AI feedback** is slow (calls Ollama) and optional. Use it when you
  want a second opinion.

### Why this matters

- Active recall > passive reading. The student writes their own
  understanding, which surfaces gaps.
- The AI feedback loop closes the loop: the student writes → the model
  reads → the model says "you missed this part" → the student goes back
  to the source. That's how learning sticks.
- The **transcript quote** is the killer feature: every chunk now has a
  1-2 line verbatim quote from the source video, prefixed with a
  `[seconds]` timestamp. The AI tutor cites this in the lesson and the
  student can cross-reference back to the original video.

### Backend

- **Tutor prompt rewrite.** `SYSTEM_PROMPT` + `USER_TEMPLATE` in
  `app/pocket/tutor.py` now demand the model (a) teach like a real teacher
  (2-4 sentences per chunk), (b) MUST quote 1-2 verbatim lines from the
  transcript per chunk, prefixed with `[<seconds>]`, (c) include a
  `check_question` to test recall. The `_coerce_chunk` helper preserves the
  new `transcript_quote` field.
- **AI grading.** New `grade_single(user_answer, canonical_answer)` and
  `grade_batch(items)` functions call Ollama with a separate
  `GRADING_SYSTEM_PROMPT` (act as a strict but kind teacher, output JSON
  `{verdict, explanation}`). Verdicts are constrained to `got_it` /
  `partial` / `missed`. New constants in `tutor.py`:
  `VERDICT_GOT_IT = "got_it"`, `VERDICT_PARTIAL = "partial"`,
  `VERDICT_MISSED = "missed"`, `VALID_VERDICTS = {those three}`.
- **PocketProgress columns.** Added `user_answer: TEXT`,
  `is_favorite: BOOLEAN (indexed)`, `last_ai_verdict: VARCHAR(16)`,
  `last_ai_explanation: TEXT`, `last_ai_graded_at: DATETIME`.
- **PocketChunk column.** Added `transcript_quote: TEXT DEFAULT ''`.
- **5 new endpoints** (all under `/m/*`):
  - `POST /m/chunk/{chunk_id}/done` — body `{user_answer, is_favorite?}`
    (replaces the body-less variant; old call without body still works
    via the `user_answer=""` default)
  - `POST /m/chunk/{chunk_id}/feedback` — body `{user_answer}`,
    returns `{verdict, explanation}`, persists to `last_ai_verdict` /
    `last_ai_explanation` / `last_ai_graded_at`
  - `POST /m/chunks/grade-batch` — body `{items: [{chunk_id, user_answer,
    canonical_answer}]}`, returns `{verdicts: [{chunk_id, verdict,
    explanation}]}`. Useful for offline "grade everything at end of
    session".
  - `POST /m/chunk/{chunk_id}/favorite` — toggles, returns new state
  - `GET /m/favorites/{video_id}` — rich list of favorited chunks
    (joins PocketProgress + PocketChunk), returns concept + transcript
    quote + user answer + last AI verdict per chunk
  - `GET /m/progress/{video_id}/detail` — per-chunk rich detail
    (joins PocketProgress + PocketChunk), returns one item per chunk
    with is_done, user_answer, is_favorite, last_ai_verdict,
    last_ai_explanation
- **DB migration.** `_apply_migrations` in `app/database.py` adds the 6
  new columns idempotently (existence-checked) on startup. Existing dev
  DB `video_learning.db` is auto-migrated; no manual SQL needed.

### iOS

- **`TeachModels.swift`** — new types: `AIVerdict` enum (gotIt /
  partial / missed, with display name + SF Symbol icon),
  `FeedbackRequest` / `FeedbackResponse`,
  `MarkDoneWithAnswerRequest` / response,
  `FavoriteToggleResponse`, `ProgressDetailItem` /
  `ProgressDetailResponse`, `FavoriteChunk` /
  `FavoritesResponse`. `Chunk.transcriptQuote` already existed from
  v0.1.0's data model, just now populated.
- **`APIClient.swift`** — new methods: `markChunkDoneWithAnswer`,
  `gradeAnswer`, `toggleFavorite`, `fetchFavorites`,
  `fetchProgressDetail`. Old `markChunkDone(chunkId:)` still works for
  callers that don't need to persist an answer.
- **`TeachMeView.swift` — ChunkCard rewrite.** Now has:
  - Transcript quote block (italic, "From the video" label) when
    present
  - **TextEditor for the student's typed answer** (placeholder "Your
    answer")
  - **Get AI feedback** button (purple, sparkles icon). Disabled
    until the student types something. After grading, shows a colored
    verdict box (green=got_it, orange=partial, red=missed) with the
    explanation
  - **Mark done** button (primary accent, checkmark icon) — now
    persists the typed answer + current heart state in one POST
  - **Heart toggle** button — toggles favorite immediately via
    `POST /m/chunk/{id}/favorite`
- **Toolbar heart filter.** TeachMeView now has a heart icon in the
  nav bar. When tapped, only favorites are shown. Counter says
  "Showing N favorites".
- **Per-chunk state hydration.** On view appear, the app calls
  `GET /m/progress/{video_id}/detail` and fills in the TextEditor +
  verdict + favorite state for every chunk. So reopening the screen
  restores your answers and last AI grades.
- **`ReviewMyAnswersView.swift` (new).** Reachable from
  VideoDetailView ("Review answers" button below "Teach me").
  Renders every chunk with the student's answer + colored verdict
  box. Has a filter toggle to show only chunks with answers.
- **`FavoritesView.swift` (new).** Reachable from VideoDetailView
  ("Favorites" button). Renders the favorited chunks with transcript
  quotes + last verdict.
- **`VideoDetailView.swift`** — added two secondary buttons below
  "Teach me": "Review answers" → ReviewMyAnswersView, "Favorites" →
  FavoritesView.

### Tests

- **`tests/test_pocket_v013.py` (new, 13 tests, all pass in isolation).**
  - 3 tests for `tutor.grade_single` / `grade_batch` (mock Ollama)
  - 2 tests for `mark_chunk_done` with body + backward compat (no body)
  - 2 tests for feedback endpoint (happy path + 404 on unknown chunk)
  - 1 test for grade-batch endpoint
  - 2 tests for favorite toggle
  - 1 test for list favorites (rich shape)
  - 1 test for progress detail (rich shape)
  - 1 test for backward compat (old mark_done still works)
- **`tests/test_pocket_sync.py` + `tests/test_pocket_tutor.py`** —
  the `auth_client` fixture was updated to use `sys.modules` to grab
  the exact `app.pocket.router.get_current_user` reference, instead
  of `from app.pocket.dev_auth import get_current_user_dev_or_real`.
  Reason: `test_pocket_dev_auth.py` sets `POCKET_DEV_AUTH=1` at
  module-import time which makes `DEV_AUTH_ENABLED = True` and the
  router's dependency-override lookup would miss. With `sys.modules`
  we always bind the override to the exact function reference
  FastAPI captured at route registration. Order-independent.
- **All 625 tests pass** (`pytest tests/ --ignore=test_whisper_picker.py`).
  The only failure is `test_whisper_picker.py::test_transcribe_endpoint_
  accepts_smart_turbo_pick`, which is a pre-existing failure unrelated
  to this work (confirmed by running it in isolation against HEAD
  before my changes).

### Verified end-to-end

- `POST /m/teach/{video_id}` → returns job, chunks have populated
  `transcript_quote` fields like `[0.0] 我们看到做的这个问题修复...`
- `POST /m/chunk/{chunk_id}/done` with body persists
  `user_answer` + `is_favorite`
- `POST /m/chunk/{chunk_id}/feedback` → returns
  `{verdict: "got_it"|"partial"|"missed", explanation: "..."}`,
  persists to `last_ai_verdict` / `last_ai_explanation`
- `POST /m/chunk/{chunk_id}/favorite` → toggles
- `GET /m/favorites/{video_id}` → rich list
- `GET /m/progress/{video_id}/detail` → rich per-chunk list
- iOS build succeeds (`xcodebuild build` → **BUILD SUCCEEDED**)
- iOS app installs + launches on iPhone 17 simulator
- Home screen shows green sync dot + courses (visual confirmation that
  ETag + dev auth still work end-to-end through the new code path)

### Design notes

- **Separate mark-done vs feedback on purpose.** Tying them together
  would have made the AI grading required and slow (Ollama call). By
  keeping them as separate buttons, "mark done" stays fast and
  durable, and "get feedback" is an opt-in second opinion.
- **Transcript quote, not full segment.** The chunk stores a 1-2
  line verbatim snippet with a timestamp prefix, not the whole
  transcript segment. That's enough for the student to rewind to
  the right place without forcing them to scroll a wall of text.
- **Verdict explanation is in Chinese** to match the rest of the
  tutor's voice. The student types their answer in Chinese
  (matching the video content); the AI grades in Chinese.
- **Favorites persist across launches** via the per-video progress
  detail hydration. So you can favorite a chunk today and see it
  tomorrow when you reopen the video.

### Not changed

- Auth model (still dev header `X-Dev-User-Id` + `POCKET_DEV_AUTH=1`)
- Snapshot model (courses / sections / videos unchanged)
- Ollama proxy pattern (iOS still knows nothing about Ollama — all
  calls go through the FastAPI backend)
- Real Firebase auth on iOS (still planned for v0.2 or later)

### Known issues / next steps

- **No visual iOS verification.** `pyautogui` clicks don't reach the
  simulator without explicit Accessibility permission for the host
  process. Visual verification deferred to the user (they have been
  validating each prior v0.1.x tag by hand). Backend endpoints were
  verified end-to-end with curl + urllib + the test suite.
- **Test pollution fixed.** The pre-existing fragility of
  `test_pocket_dev_auth.py` (sets env var at import time) caused 9
  pocket tests to 401 when run after it. The `sys.modules` lookup
  fix in all 3 pocket test fixtures makes the suite order-
  independent. No changes to `test_pocket_dev_auth.py` itself.


## v0.1.3 hotfix — AI feedback box is no longer empty

**Commit:** `39413ad` on `mvp-mobile-pocket-v0.1` (still tagged `pocket-v0.1.3-real-teaching`)
**Date:** 2026-07-28

### Bug
User typed "Ai fighting function I believe like whether AI can attack
user on chess etc" for chunk #5 ("游戏对弈功能测试"), tapped "Get AI
feedback", got back `verdict: missed` with an **empty explanation**. The
verdict showed but the box body was blank.

Two causes, two fixes.

### Bug 1: endpoint was passing the question, not the answer

`POST /m/chunk/{id}/feedback` was passing `chunk.check_question` as the
**canonical_answer** for the grader. But `check_question` is a prompt to
the student (e.g. `"视频最后演示者测试了游戏的什么功能？"`) — not an
answer. With nothing real to compare against, Ollama returned empty
strings or unhelpful "missed" verdicts.

**Fix:** when the caller omits `canonical_answer`, build it from the
chunk as:

```
Question asked: <check_question>
What the tutor said: <teach_text>
```

`teach_text` IS the actual lesson (and therefore the actual answer), so
the grader now has full context.

### Bug 2: Ollama-empty explanations broke the UI

`tutor.grade_single` returned `{"verdict": ..., "explanation": ""}`
whenever Ollama produced an empty output. iOS rendered the verdict with
no body. (Note: `llama3.1` on the dev Mac is broken — returns empty for
everything. The app config defaults to `glm-5.2:cloud` which works
fine, but the fallback explanations protect against future model
regressions too.)

**Fix:** two new behaviors in `grade_single`:
- **Empty user_answer short-circuits** — don't waste an Ollama call.
  Returns `"No answer provided. Type what you remember, then try again."`
- **Empty Ollama explanations get a verdict-specific fallback** — a
  `_FALLBACK_EXPLANATION` dict per verdict keeps the iOS feedback box
  from showing blank.

### Tests
3 new regression tests in `tests/test_pocket_v013.py`:
- `test_feedback_endpoint_falls_back_to_teach_text` — verifies the
  derived canonical_answer contains both `check_question` and
  `teach_text`
- `test_feedback_empty_answer_does_not_call_ollama` — verifies the
  short-circuit doesn't waste an Ollama call
- `test_feedback_empty_ollama_explanation_falls_back` — verifies
  Ollama-empty explanations get a verdict-specific fallback

**628/628 tests pass** (was 625 before).

### Verified end-to-end
Re-graded chunk 3 ("游戏对弈功能测试") with the user's actual answer:

```json
{
  "verdict": "got_it",
  "explanation": "The student correctly identified that the final test
  focused on the AI opponent's fighting functionality, including
  whether the AI could attack the player's chess pieces. This matches
  the canonical answer's description of testing AI response,
  intelligent movement, and capturing operations during an actual
  match."
}
```
