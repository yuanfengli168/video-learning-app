# MVP1 — Successfully Finished 🎉

> **Status:** ✅ All MVP1 scope items from [`doc/design.md`](design.md) §2 + §3 + §4 + §5 are implemented, tested, and pushed to `origin/main`.
> **Completion date:** 2026-07-06
> **Test count:** 218 passing · **Coverage:** 96% (target was ≥90%)

---

## 1. What was delivered

MVP1 is the "local single-user" foundation: a FastAPI web app that lets a signed-in user upload local videos, transcribe them with Faster-Whisper, generate learning materials (summary, mindmap, quiz, flashcards, topic timestamps) via a local Ollama LLM, and chat with the AI about the content.

### Tech stack — exactly as specified

| Concern | Decision | Where it lives |
|---|---|---|
| API | FastAPI | `app/main.py`, `app/routers/` |
| Frontend | Jinja2 templates + vanilla JS (HTMX dropped for interactive widgets per the design's "vanilla JS for interactive widgets" clause) | `app/templates/` |
| Styling | Tailwind CSS via CDN, `darkMode: 'class'` for dark/light toggle | `app/templates/base.html` |
| Database | SQLite + SQLAlchemy 2.0; schema via `Base.metadata.create_all()` (Alembic deferred to MVP2) | `app/database.py`, `app/models/` |
| Storage | Local filesystem (`uploads/`, `storage/`, both gitignored) | `app/routers/videos.py` |
| Processing | Synchronous — acceptable for single-user local use | `app/services/transcription.py`, `app/services/llm.py` |
| Auth (frontend) | [AuthKit](https://github.com/yuanfengli168/authkit) — Firebase Auth UI (Google + Email) | `app/templates/login.html` |
| Auth (backend) | Firebase Admin SDK + httpOnly session cookie flow | `app/auth/` |
| Transcription | Faster-Whisper with `tiny`/`base`/`small`/`medium` selectable on the web UI | `app/services/transcription.py`, `whisper-model` dropdown in `video.html` |
| LLM | Ollama at `http://localhost:11434`, model `glm-5.2:cloud` | `app/services/llm.py` |
| LLM determinism | `temperature: 0` + `seed: 42` — re-generating the same transcript always produces the same materials | `app/services/llm.py:125-126` |
| Testing | pytest + pytest-asyncio + httpx; Whisper + Ollama mocked; coverage target ≥90% | `tests/`, `scripts/test.sh` |
| Dependencies | `requirements.txt` (no Poetry for MVP1) | repo root |

---

## 2. §2 Architecture scorecard — 10 / 10

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | FastAPI local | ✅ | `app/main.py` |
| 2 | Jinja2 + HTMX/vanilla JS | ✅ | `app/templates/*.html` |
| 3 | Tailwind (dark/light) | ✅ | `base.html` (`darkMode: 'class'`) |
| 4 | SQLite + SQLAlchemy | ✅ | `app/database.py` |
| 5 | `Base.metadata.create_all()` | ✅ | `app/database.py:init_db()` |
| 6 | Local file storage | ✅ | `uploads/`, `storage/` (gitignored) |
| 7 | Synchronous processing | ✅ | `app/routers/videos.py` |
| 8 | AuthKit + Firebase Admin | ✅ | `app/templates/login.html`, `app/auth/` |
| 9 | Faster-Whisper + model select | ✅ | `app/services/transcription.py` |
| 10 | Ollama + temp=0/seed=42 | ✅ | `app/services/llm.py` |

---

## 3. §3 Data Model scorecard — 6 / 6

All entities from the design are in `app/models/`:

| Entity | File | Notes |
|---|---|---|
| `Course` | `course.py` | Top-level hierarchy node |
| `Section` | `section.py` | Module/week within a course |
| `Video` | `video.py` | Individual class file |
| `Asset` | `asset.py` | Generated material (summary, transcript, flashcards, quiz, mindmap, **topic_timestamps**) |
| `ChatSession` | `chat.py` | Per-flashcard chat thread |
| `ChatMessage` | `chat.py` | User/assistant messages within a session |

---

## 4. §4 AI Processing Pipeline — 4 / 4

1. ✅ **Input** — user picks a local file or drag-and-drops into the dashboard upload zone
2. ✅ **Transcription** — Faster-Whisper extracts audio via ffmpeg, produces timestamped per-sentence segments
3. ✅ **LLM generation** — single Ollama call with `temperature: 0` + `seed: 42` returns JSON with: summary, mindmap, quiz, flashcards, **and `topic_timestamps`**
4. ✅ **Storage** — assets saved to the DB, files saved to `storage/`

The `topic_timestamps` list is consumed by the clickable mindmap (see §5 below).

---

## 5. §5 Features scorecard — 8 / 8 (1 deferred)

| # | Feature | Status | Implementation |
|---|---|---|---|
| 1 | Chat Interface ("Teach me real-world usage" button on flashcards) | ✅ | `app/templates/video.html`, `app/routers/chat.py`, `app/services/chat.py` |
| 2 | Chat History page (search, continue, delete) | ✅ | `app/templates/chat_history.html`, `app/routers/chat.py` |
| 3 | Transcript Viewer (click-to-seek, search w/ highlight + prev/next) | ✅ | `app/templates/video.html` (`searchTranscript`, `searchNavigate`, `seekTo`) |
| 4 | Clickable Mindmap (banner + ancestor fallback + toast) | ✅ | `app/templates/video.html` (`jumpToTopic`, `buildMindmapParentMap`, `findTopicTimestampWithAncestors`, `showToast`) |
| 5 | Mindmap Controls (zoom, fit, drag/pan, scroll-zoom, Ctrl+0 reset) | ✅ | `app/templates/video.html` (`attachMindmapInteraction`, `fitMindmapSVG`, `inlineMindmapZoom`, `mindmapZoom`) — incl. the markmap-d3-zoom fix landed in commit `0eb3878` |
| 6 | Session-based Auth (httpOnly `fb_token` cookie) | ✅ | `app/auth/session.py` (`COOKIE_NAME = "fb_token"`, `set_cookie`, `delete_cookie`) |
| 7 | Sidebar Search (real-time filter, "No matches" placeholder) | ✅ | `app/templates/base.html` (`filterSidebarCourses`) |
| 8 | Video Downloader (`yt-dlp` integration) | ⏭️ **Deferred** | Explicitly marked "(Future)" in `doc/design.md` §5 — NOT in MVP1 scope |

---

## 6. Bug fixes shipped alongside MVP1

These are the bug-fix commits that turned the "feature-complete" code into "polished, demo-ready" MVP1:

| Commit | Fix |
|---|---|
| `92a58f1` | Walk up mindmap tree to find ancestor timestamp for leaf nodes |
| `c5bfc1d` | 3 mindmap UX issues: banner position, fullscreen auto-fit, post-fullscreen layout |
| `72e8841` | Sidebar search filters courses by title |
| `c19dac0` | Sidebar search ReferenceError on highlight + UX polish |
| `b112f60` | Mobile sidebar toggle: hamburger (☰) / close (✕) based on state |
| `edc0bdd` | Docs for hamburger toggle |
| `d2e2b0c` | Video page overflows on mobile (video element forces horizontal scroll) |
| `f167fe0` | Inline mindmap had no pan/zoom + auto re-fit blew away user gestures |
| `3ce42f0` | Dashboard upload zone was a stub — now actually uploads |
| `0eb3878` | Disable markmap's built-in d3-zoom so drag-to-pan works after zooming |

Each fix is documented in the corresponding commit message and was followed by a "Docs: ..." commit that bumped the test count and updated the relevant section of `doc/handover.md`.

---

## 7. Test coverage breakdown

```
TOTAL                             710     29    96%
====================== 218 passed in 2.05s ======================
```

| Module | Coverage | Notes |
|---|---|---|
| `app/models/*.py` | 100% | All 6 entities |
| `app/routers/frontend.py` | 100% | Template routes |
| `app/services/transcription.py` | 100% | Whisper pipeline |
| `app/auth/*.py` | 100% | Auth + session cookie |
| `app/routers/chat.py` | 96% | 3 lines: error paths |
| `app/routers/generation.py` | 95% | 3 lines: error paths |
| `app/services/llm.py` | 91% | 4 lines: retry/fallback paths |
| `app/routers/courses.py` | 90% | 8 lines: not-found branches |
| `app/routers/videos.py` | 90% | 11 lines: file-not-found, expired paths |

Untested lines are error-handling branches exercised only by edge cases that the mocked unit tests don't reach. The integration tests under `tests/integration/` (not run in `pytest -q` because they require real Ollama + Whisper) cover the real-Ollama and real-Whisper paths.

---

## 8. How to demo MVP1

```bash
# 1. One-time setup
bash scripts/setup.sh
bash scripts/setup_firebase_key.sh    # paste your Firebase service-account.json

# 2. Start the app
bash scripts/start.sh                 # boots Ollama + uvicorn

# 3. Open http://localhost:8000
#    - Sign in with Google or Email (via AuthKit)
#    - Create a course → add a section → upload a video
#    - Pick a Whisper model (start with "base"), click Transcribe
#    - Click "Generate Materials" → switch through Summary/Flashcards/Quiz/Mindmap
#    - Click any mindmap node to jump to that topic
#    - Click "💡 Teach me real-world usage" on a flashcard to start a chat
#    - Visit /chat-history to see all your past chats

# 4. Run the test suite
bash scripts/test.sh
```

---

## 9. Known limitations (intentional, deferred to MVP2)

| Limitation | MVP2 fix |
|---|---|
| Single-user only | Multi-tenant auth (OAuth2 + paid tiers) |
| Synchronous LLM/Whisper blocks the request | Celery + Redis task queue |
| SQLite loses data on filesystem wipe | PostgreSQL (Neon) |
| Files in `uploads/`/`storage/` are not backed up | S3 / MinIO |
| No Alembic — schema changes require a wipe | Alembic migrations |
| 96% coverage leaves a few error branches untested | Integration-test sweep |

These are all in `doc/design.md` §2 (MVP2) and are NOT blockers for MVP1 sign-off.

---

## 10. Recommended next steps

See the answer to "what should we do now?" in the chat log (after this doc was created). The recommended path is:

1. **Tag this commit as `v1.0.0`** so MVP1 is recoverable by SHA
2. **Record a 2-minute demo video** walking through §8 above (good for portfolio / README)
3. **Decide MVP2 scope** by reading [`doc/design.md`](design.md) §2 "MVP2 (Cloud Scalable)" and pulling 3-5 items that deliver the most user value per week of work
4. **Set up CI** (GitHub Actions) to run `bash scripts/test.sh` on every PR — currently we have no CI, so the 96% coverage number is only verified locally
5. **Write a CHANGELOG.md** so future-you can see what landed when (the git log has this info, but a human-curated CHANGELOG is friendlier for collaborators)

---

**Signed off:** 2026-07-06 · commit `754d614` · 218 tests · 96% coverage · `origin/main` is green.
