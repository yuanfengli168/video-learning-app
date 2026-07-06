# Handover & Development Guide

This document serves as a guide for developers taking over or contributing to the Video Learning App.

## 1. Prerequisites
*   **Python:** Version 3.11 or higher (`python3 --version`).
*   **Ollama:** Installed and running at `http://localhost:11434`. Ensure the `glm-5.2:cloud` model is accessible (`ollama pull glm-5.2:cloud`).
*   **FFmpeg:** Required for audio extraction (`brew install ffmpeg`).
*   **AuthKit:** The [AuthKit](https://github.com/yuanfengli168/authkit) repository provides the frontend Firebase Auth UI. A Firebase project with Google + Email/Password auth enabled is required. Place your Firebase config in `.env` (see `.env.example`).

## 2. Environment Setup
```bash
# Quick setup (recommended)
./scripts/setup.sh

# Manual setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in Firebase + Ollama config
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 3. Tech Stack Decisions (MVP1)
| Concern | Decision | Rationale |
|---------|----------|-----------|
| Frontend rendering | Jinja2 + HTMX + vanilla JS | SPA-like UX with server-side simplicity; vanilla JS for interactive widgets (mindmap, search, chat) |
| Styling | Tailwind CSS | Utility-first, responsive; dark + light themes via `class` strategy |
| Database | SQLite + SQLAlchemy | `Base.metadata.create_all()` for MVP1; Alembic deferred to MVP2 |
| Auth (frontend) | AuthKit (Firebase Auth UI) | Drop-in Google + email/password login |
| Auth (backend) | Firebase Admin SDK + session cookie | Frontend exchanges Firebase ID token for an httpOnly session cookie via `POST /api/auth/session`; subsequent API calls authenticate via the cookie (no tokens in JS) |
| Transcription | Faster-Whisper | Model selectable on web UI (`tiny`/`base`/`small`/`medium`); auto-downloads |
| LLM | Ollama (`glm-5.2:cloud`) | Local inference at `localhost:11434`; calls use `temperature: 0` + `seed: 42` for deterministic output |
| Testing | pytest + pytest-asyncio + httpx | Mock Whisper/Ollama in unit tests; integration tests marked slow |
| Coverage target | ≥90% backend logic | 100% coverage is not required; focus on core logic |
| Dependencies | `requirements.txt` | No Poetry for MVP1 |

## 4. Build Order (6 Phases)
Each phase produces 3 commits: (A) implementation, (B) tests, (C) fix failing tests.

1. **Project scaffold + config + DB models** — FastAPI app structure, SQLAlchemy models (Course → Section → Video → Asset), config via `.env`.
2. **Auth** — AuthKit frontend integration + Firebase Admin SDK backend token verification + session cookie flow.
3. **Video upload + Whisper transcription** — Upload endpoint, model selection, Faster-Whisper pipeline, timestamped transcript storage.
4. **LLM generation** — Ollama integration; prompt engineering for JSON output (summary, mindmap, quiz, flashcards, topic_timestamps).
5. **Frontend views** — Jinja2 + HTMX + Tailwind; all views from spec (dashboard, course, video player, tabs); dark/light themes; transcript search with highlight + navigation; mindmap zoom/pan/fit + fullscreen; **clickable mindmap nodes with ancestor-walking timestamp lookup and non-blocking toast notifications**.
6. **Chat interface** — ChatGPT-style chat triggered by flashcard "Teach me real-world usage" button; persisted history.

## 5. Running Tests
```bash
# Recommended
./scripts/test.sh

# Manual
pytest                    # unit tests (Whisper/Ollama mocked)
pytest -m slow           # integration tests (requires real Ollama + Whisper)
pytest --cov=app         # coverage report
```

> **Current status:** 206 tests passing, 96% backend coverage.

### Sidebar Search

The sidebar's "Search courses..." input filters the user's course list in real time as the user types. It's a case-insensitive substring match against each course's title (via a `data-title` attribute). The implementation is in `app/templates/base.html` (`filterSidebarCourses` function). The `_ctx()` helper in `app/routers/frontend.py` now fetches the user's courses when a DB session is provided, so the sidebar shows the same course list on every page (not just the dashboard).

### Chat History Page

`/chat-history` is a two-pane Jinja page (see `app/templates/chat_history.html`) that lists all the user's past flashcard chat sessions, lets them continue any conversation, and delete ones they don't need. The backend API (`/api/chat/sessions` and friends) was already in place — only the UI was missing. Unauthenticated users see a sign-in prompt; signed-in users see the list + composer.

### Mindmap Parent-Map Algorithm

The LLM only generates `topic_timestamps` for the *most important* topics (typically parent / level-1 branches). To make leaf nodes clickable too, the frontend builds a parent map from the mindmap markdown at load time and walks up the tree when a node has no exact timestamp. The algorithm is implemented in `app/templates/video.html` as `buildMindmapParentMap` and `findTopicTimestampWithAncestors`. The same algorithm is ported to Python in `tests/test_mindmap_parent_map.py` to serve as a regression contract — if the JS changes, update the Python test to match.

When no ancestor matches, a **non-blocking toast** is shown in the bottom-right corner (no `alert()` dialog), so the user can keep interacting with the mindmap.

## 6. Project Structure
```
video-learning-app/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry + router registration
│   ├── config.py            # Settings via pydantic-settings
│   ├── database.py          # SQLAlchemy engine + session + init_db
│   ├── models/              # ORM models
│   │   ├── __init__.py
│   │   ├── asset.py         # Asset (summary, transcript, flashcards, quiz, mindmap, topic_timestamps)
│   │   ├── chat.py          # ChatSession + ChatMessage
│   │   ├── course.py        # Course
│   │   ├── section.py       # Section
│   │   └── video.py         # Video
│   ├── routers/             # API route modules
│   │   ├── __init__.py
│   │   ├── auth.py          # /api/auth/me — returns current user
│   │   ├── session.py       # /api/auth/session — issue/clear httpOnly session cookie
│   │   ├── chat.py          # /api/chat/sessions (CRUD + send message)
│   │   ├── courses.py       # /api/courses (CRUD + sections)
│   │   ├── frontend.py      # Jinja2 template routes (/, /course, /video, /login, /chat-history)
│   │   ├── generation.py    # /api/generate (LLM materials + get assets incl. topic_timestamps)
│   │   └── videos.py        # /api/videos (upload, transcribe, file serving)
│   ├── services/            # Business logic
│   │   ├── __init__.py
│   │   ├── chat.py          # Ollama chat integration
│   │   ├── llm.py           # Ollama LLM generation + JSON extraction (incl. topic_timestamps prompt)
│   │   └── transcription.py # Faster-Whisper transcription
│   ├── auth/                # Auth middleware
│   │   ├── __init__.py
│   │   ├── dependencies.py  # get_current_user, get_current_user_optional (cookie + Bearer fallback)
│   │   ├── firebase_admin.py # Firebase Admin SDK init + token verification
│   │   └── session.py       # Session cookie helpers (COOKIE_NAME, set/clear)
│   └── templates/           # Jinja2 HTML templates
│       ├── base.html        # Layout: sidebar (with course search), header, dark/light theme toggle
│       ├── dashboard.html   # Home: upload zone, courses grid
│       ├── course.html      # Course: sections accordion, video upload
│       ├── video.html       # Video player + tabs + clickable mindmap nodes + topic banner
│       ├── chat_history.html # Two-pane chat history (list + detail + composer)
│       ├── login.html       # AuthKit login page
│       ├── error.html       # Error page
│       └── redirect.html    # Redirect helper
├── tests/                   # 206 pytest tests (96% coverage)
│   ├── conftest.py          # Fixtures: test DB, client
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_main.py
│   ├── test_model_*.py      # Model tests (course, section, video, asset, chat)
│   ├── test_firebase_admin.py
│   ├── test_auth.py
│   ├── test_session.py
│   ├── test_transcription.py
│   ├── test_courses.py
│   ├── test_videos.py
│   ├── test_llm.py
│   ├── test_generation.py
│   ├── test_frontend.py
│   ├── test_ui_features.py  # Frontend template feature tests (banner, click handlers, etc.)
│   ├── test_mindmap_parent_map.py # Python port of the JS ancestor-walking algorithm (regression contract)
│   ├── test_chat_service.py
│   └── test_chat_router.py
├── scripts/                 # Helper shell scripts
│   ├── setup.sh             # Initial setup (venv, deps, .env)
│   ├── setup_firebase_key.sh # Place Firebase service account JSON in the right spot
│   ├── start.sh             # Start Ollama + uvicorn for local dev
│   └── test.sh              # Run the full test suite
├── doc/
│   ├── design.md
│   ├── handover.md
│   └── deployment.md
├── .env.example
├── .gitignore
├── requirements.txt
└── Readme.md
```

## 7. Deployment Guide

### Local Development
```bash
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
App runs at `http://localhost:8000`. Ollama must be running locally at `localhost:11434`.

### Remote Deployment (Free Tier)

**Architecture:** Frontend templates are served by the FastAPI backend (Jinja2). There is no separate frontend build — the backend renders HTML directly. So we only need to deploy the backend.

**Option A: Render.com (Recommended — Free Tier)**
1. Push your code to GitHub (already done).
2. Go to [render.com](https://render.com) → New → Web Service.
3. Connect your GitHub repo `yuanfengli168/video-learning-app`.
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
5. Add environment variables (same as `.env.example`):
   - `DATABASE_URL` — use a free PostgreSQL from [Neon](https://neon.tech) or [Supabase](https://supabase.com)
   - `OLLAMA_BASE_URL` — needs a remote Ollama instance (see below)
   - `FIREBASE_*` — your Firebase config
   - `FIREBASE_SERVICE_ACCOUNT_KEY_PATH` — path to your service account JSON
6. Note: Free tier sleeps after 15 min idle, takes ~30s to wake up.

**Option B: Fly.io (Free Tier — 3 shared VMs)**
1. Install `flyctl`: `brew install flyctl`
2. `fly launch` in the project root
3. Add a `Dockerfile` (Fly requires it)
4. `fly deploy`
5. Free tier: 3 shared-cpu-1x VMs with 256MB RAM.

**Option C: Railway.app (Free Trial → $5/month)**
- Similar to Render but no free tier after trial.

**Ollama Remote Hosting:**
Ollama needs to run somewhere accessible by the backend. Options:
- Run Ollama on a free VM (Oracle Cloud Free Tier offers always-free ARM VMs)
- Use a managed Ollama API service
- For MVP1 testing: run Ollama locally and use a tunnel (ngrok/cloudflare tunnel) to expose it

**Firebase Authorized Domains:**
After deploying, add your domain (e.g., `your-app.onrender.com`) to:
Firebase Console → Authentication → Settings → Authorized Domains.

## 8. Detailed Frontend Layout & UI Specifications

### Global Layout Structure
*   1 Sidebar + 1 Main Content Area.
*   **Desktop:** Fixed Sidebar (left, 250px). Main area takes remaining space.
*   **Mobile:** Sidebar hidden. Hamburger menu (☰) in top-left opens it as an overlay drawer.
*   **Top Header Bar:** Spans main area. Left: Hamburger/Logo. Center: Breadcrumbs. Right: User Profile/Logout.

### Sidebar Contents
*   Global Search Bar (filters the course list in real time as you type).
*   Navigation: `Dashboard` (Home), `My Courses` (Expandable list), `Chat History`.
*   Footer: User Avatar, Settings.
*   Mobile: the sidebar logo has a toggle button that swaps between a hamburger (☰) and a close (✕) icon depending on whether the sidebar is open or closed.

### Homepage (Dashboard) Layout
*   **Upload Zone:** Large drag-and-drop box at the top for video files.
*   **Continue Learning:** Horizontal carousel of in-progress videos (Thumbnail, Title, Progress Bar).
*   **Your Courses:** Responsive grid (3 cols desktop, 1 col mobile) of Course Cards. Clicking opens Course View.

### Course View Layout
*   Header: Course Title.
*   Main Area: List of `Sections` as collapsible accordions. Expanding a Section lists its `Videos`. Clicking a Video opens the Video Player View.

### Video Player View Layout (Core Learning Page)
*   **Desktop (Split):**
    *   Left (60%): Video Player + Interactive Transcript with search bar below it.
    *   Right (40%): Tabbed interface (`Summary`, `Flashcards`, `Quiz`, `Mindmap`, `Chat`).
*   **Mobile (Stacked):**
    *   Top: Video Player.
    *   Middle: Tabbed interface (Transcript moved into a tab to save space).
*   **Tabs Content:**
    *   *Flashcards:* Flippable cards. Includes "Teach me real-world usage" button.
    *   *Mindmap:* Interactive Markmap HTML.
    *   *Chat:* ChatGPT-style interface.

### Chat Interface Layout
*   Triggered by "Teach me real-world usage" button. Takes over right panel (Desktop) or full screen (Mobile).
*   Header: "Real-World Usage: [Concept]" + Back button.
*   Body: Alternating chat bubbles (User right, AI left).
*   Footer: Text input + Send button. History saved to DB.