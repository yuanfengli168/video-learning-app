# MVP1.0 Post-Release Critical Review

> **Date:** 2026-07-08
> **Scope:** Architecture, security, and feature gaps identified after v1.0.0 release
> **Status:** Review only — no implementation changes

---

## 🔴 Critical Issues

### 1. In-memory job state is lost on server restart

`app/jobs.py` uses a module-level `_jobs` dict to track background transcription and generation progress. If uvicorn restarts or crashes mid-job, all progress is gone. The UI polls `GET /api/videos/{id}/status` and gets `null` — the video stays stuck in `"transcribing"` or `"generating"` forever with no way to recover or retry.

The `last_transcribe_job` and `last_generate_job` columns on the Video model survive page refreshes but **not** server restarts, since the in-memory dict is the source of truth for active jobs.

**Impact:** Any server restart during a long-running Whisper transcription (5–15 minutes for medium model on a 1-hour video) leaves the video in a broken state.

---

### 2. `duration` column type mismatch

`Video.duration` is declared as `Mapped[float]` in the ORM model but mapped to `Integer` in the database column:

```python
duration: Mapped[float] = mapped_column(Integer, default=0)  # seconds
```

A video with duration `9.5` seconds will be truncated to `9` by SQLite. This affects the transcript viewer's timestamp display and any time-based calculations.

**Impact:** Sub-second precision is lost. The column type should be `Float`, not `Integer`.

---

### 3. Cookie `Secure=False` in production

`app/auth/session.py` hardcodes `secure=False`:

```python
response.set_cookie(
    key=COOKIE_NAME,
    value=body.id_token,
    max_age=COOKIE_MAX_AGE,
    httponly=True,
    samesite="lax",
    secure=False,  # ← hardcoded
)
```

The SECURITY.md says "True in production (HTTPS)" but there's no logic to toggle it based on environment. If deployed with HTTPS (Render, Cloudflare, etc.), the session cookie will still be sent over plain HTTP — exposing it to MITM attacks.

**Impact:** Session token interception on any non-localhost deployment.

---

### 4. No file cleanup on video/course deletion

When a course or video is deleted via the API, SQLAlchemy `CASCADE` removes the database rows but the actual video file in `uploads/` stays on disk forever. Over time, deleted content fills storage without any way to reclaim it.

**Impact:** Disk leak that grows with usage. On a VPS with limited storage, this can fill the disk.

---

### 5. LLM response parsing is fragile

`_extract_json()` in `app/services/llm.py` tries three fallback strategies to parse the LLM's response:

1. Direct `json.loads()`
2. Strip markdown code fences (`` ```json ... ``` ``)
3. Regex for first `{ ... }` block

If Ollama returns malformed JSON (very common with LLMs — truncated output, mixed markdown/JSON, etc.), the entire generation fails with a `ValueError`. There's no retry mechanism and no partial save. The user sees `"error"` status on the video and must manually re-trigger generation.

**Impact:** Generation failures are common with longer transcripts, and the user has no recourse but to retry.

---

## 🟡 Architectural Concerns

### 6. Background tasks run in-process

FastAPI's `BackgroundTasks` runs in the same uvicorn worker process as the API. Whisper `medium` model on a 1-hour video uses significant CPU and RAM (~2GB model load + processing). If a user triggers transcription while the API is serving other requests, response times will degrade severely.

This is documented as acceptable for MVP1 (single-user), but it means concurrent usage (even by the same user) is problematic.

**Impact:** One transcription can make the entire app unresponsive.

---

### 7. No concurrency guard on transcribe/generate

A user can POST `/api/videos/{id}/transcribe` twice for the same video. The second call overwrites the first job in `_jobs`, but the first `BackgroundTask` continues running. Two Whisper models loaded simultaneously will cause OOM on most machines.

There's also no check for "is this video already being transcribed?" before starting a new job.

**Impact:** Duplicate jobs waste resources and can crash the server.

---

### 8. SQLite + concurrent writes

SQLite only supports one writer at a time. If two background tasks (e.g., transcribe finishes while generate is also running) try to commit simultaneously, you'll get `OperationalError: database is locked`. The `check_same_thread=False` setting enables multi-thread access but doesn't solve write contention.

**Impact:** Intermittent 500 errors when multiple background tasks finish close together.

---

### 9. No Alembic / migration tool

The hand-rolled `_apply_migrations()` in `database.py` only handles additive column changes (`ALTER TABLE ADD COLUMN`). Any column rename, type change, or column removal requires a manual database wipe and recreation.

This is documented as deferred to MVP2, but it means any schema evolution during MVP1.x development is risky — there's no rollback path.

**Impact:** Schema changes require database recreation; data loss risk.

---

## 🟡 Security Concerns

### 10. No rate limiting on auth endpoints

`/api/auth/session` accepts unlimited POST requests with no rate limiting. An attacker could brute-force Firebase ID tokens or DoS the session creation endpoint.

This is documented as an MVP2 concern, but it's worth noting for any public deployment.

**Impact:** Potential auth abuse on publicly accessible instances.

---

### 11. CSP allows `'unsafe-eval'` and `'unsafe-inline'`

The Content Security Policy in `app/middleware.py` includes:

```
script-src 'self' 'unsafe-inline' 'unsafe-eval' ...
```

This is required because Markmap uses d3 internals that call `eval()`. However, it significantly weakens XSS protection. If any user-generated content (transcript text, flashcard terms, quiz answers) ends up in an `eval`-able context, it's a stored XSS vector.

**Impact:** Reduced XSS defense; acceptable trade-off for the d3 requirement, but worth monitoring.

---

### 12. Ollama is unauthenticated

`OLLAMA_BASE_URL` defaults to `http://localhost:11434` with no API key or authentication. The deployment guide instructs users to "open port 11434" on their Oracle VM — this creates an open LLM proxy that anyone on the internet can use.

**Impact:** Anyone who discovers the VM's IP can send chat/completion requests to your Ollama instance for free.

---

## 🟡 Missing Features / Gaps

### 13. No video download (yt-dlp)

Listed in `Todo.md` and `doc/design.md` §5 as a future feature but not implemented. Users can only upload local files — no way to pull from YouTube, Bilibili, or other video platforms.

---

### 14. No language selection

Whisper auto-detects the language, but for Cantonese/Traditional Chinese users there's no override. A Traditional Chinese video will produce Traditional Chinese output with no way to convert to Simplified. `Todo.md` has a detailed plan (Whisper `language` param + OpenCC post-processing) but no code.

---

### 15. No progress bar in the UI

The backend tracks progress in `jobs.py` and the `/status` endpoint returns `pct`, `eta_seconds`, and `message`. However, the Jinja2 templates don't poll this endpoint — the user sees a spinner with no indication of progress during transcription (5–15 min) or generation (30–60s).

---

### 16. Chat has no streaming

`chat_with_ollama()` uses `stream: False` with a 120-second timeout. The user stares at a blank screen for up to 2 minutes per message. Ollama supports SSE streaming, which would provide token-by-token output.

---

### 17. No friendly error when Ollama is down

If Ollama isn't running, the user gets a generic 500 Internal Server Error or a timeout. There's no health check or friendly "Ollama is not running — please start it first" message on the frontend.

---

### 18. No pagination on list endpoints

`list_courses` and `list_chat_sessions` return all records for a user. At scale (100+ courses, 1000+ chat sessions), these responses become slow and memory-heavy.

---

## 🟢 Minor / Polish

### 19. Unused `ollama` dependency

`requirements.txt` includes `ollama>=0.4.4`, but the code uses `httpx` directly to call Ollama's REST API. The `ollama` package is never imported anywhere.

---

### 20. CI tests Python 3.14 (beta)

The GitHub Actions CI matrix tests Python 3.14, which is still in beta. Consider adding 3.11 or 3.13 as a stable target instead of (or in addition to) 3.14.

---

### 21. Verify `.gitignore` completeness

SECURITY.md states that `uploads/`, `storage/`, `*.db`, and `firebase-service-account.json` are gitignored. Worth verifying these patterns actually exist in `.gitignore` and haven't been accidentally removed.

---

### 22. `Video.duration` column truncates floats

Same root cause as issue #2 — `Integer` column for a `float` field. Any migration path needs to handle the type change from `Integer` to `Float`.

---

## Priority Summary

| Priority | # | Issue | Effort |
|----------|---|-------|--------|
| 🔴 Critical | 1 | Job state lost on restart | Medium |
| 🔴 Critical | 2 | Duration column type mismatch | Small |
| 🔴 Critical | 3 | Cookie Secure=False hardcoded | Small |
| 🔴 Critical | 4 | No file cleanup on deletion | Small |
| 🔴 Critical | 5 | LLM JSON parsing fragile | Medium |
| 🟡 Arch | 6 | Background tasks in-process | Large |
| 🟡 Arch | 7 | No concurrency guard on jobs | Medium |
| 🟡 Arch | 8 | SQLite write contention | Medium |
| 🟡 Arch | 9 | No Alembic migrations | Medium |
| 🟡 Security | 10 | No rate limiting | Small |
| 🟡 Security | 11 | CSP unsafe-eval | Accepted |
| 🟡 Security | 12 | Ollama unauthenticated | Small |
| 🟡 Gap | 13 | No yt-dlp download | Medium |
| 🟡 Gap | 14 | No language selection | Medium |
| 🟡 Gap | 15 | No progress bar UI | Medium |
| 🟡 Gap | 16 | No chat streaming | Medium |
| 🟡 Gap | 17 | No Ollama-down message | Small |
| 🟡 Gap | 18 | No pagination | Small |
| 🟢 Minor | 19 | Unused ollama dep | Trivial |
| 🟢 Minor | 20 | CI Python 3.14 beta | Trivial |
| 🟢 Minor | 21 | Verify .gitignore | Trivial |
| 🟢 Minor | 22 | Duration float truncation | Duplicate of #2 |

---

_This review was generated from a full code audit of the v1.0.0 codebase (commit 38a2f67)._