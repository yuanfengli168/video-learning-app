# Handover & Development Guide

This document serves as a guide for developers taking over or contributing to the Video Learning App.

## 1. Prerequisites
*   **Python:** Version 3.11 or higher (`python3 --version`).
*   **Ollama:** Installed and running at `http://localhost:11434`. Ensure the `glm-5.2:cloud` model is accessible (`ollama pull glm-5.2:cloud`).
*   **FFmpeg:** Required for audio extraction (`brew install ffmpeg`).
*   **AuthKit:** The [AuthKit](https://github.com/yuanfengli168/authkit) repository provides the frontend Firebase Auth UI. A Firebase project with Google + Email/Password auth enabled is required. Place your Firebase config in `.env` (see `.env.example`).

## 2. Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Fill in Firebase + Ollama config
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 3. Tech Stack Decisions (MVP1)
| Concern | Decision | Rationale |
|---------|----------|-----------|
| Frontend rendering | Jinja2 + HTMX | SPA-like UX with server-side simplicity; no JS framework needed |
| Styling | Tailwind CSS | Utility-first, responsive; dark + light themes via `class` strategy |
| Database | SQLite + SQLAlchemy | `Base.metadata.create_all()` for MVP1; Alembic deferred to MVP2 |
| Auth (frontend) | AuthKit (Firebase Auth UI) | Drop-in Google + email/password login |
| Auth (backend) | Firebase Admin SDK | Verify Firebase ID tokens as JWT middleware on protected routes |
| Transcription | Faster-Whisper | Model selectable on web UI (`base`/`small`/`medium`); auto-downloads |
| LLM | Ollama (`glm-5.2:cloud`) | Local inference at `localhost:11434` |
| Testing | pytest + pytest-asyncio + httpx | Mock Whisper/Ollama in unit tests; integration tests marked slow |
| Coverage target | ≥90% backend logic | 100% coverage is not required; focus on core logic |
| Dependencies | `requirements.txt` | No Poetry for MVP1 |

## 4. Build Order (6 Phases)
Each phase produces 3 commits: (A) implementation, (B) tests, (C) fix failing tests.

1. **Project scaffold + config + DB models** — FastAPI app structure, SQLAlchemy models (Course → Section → Video → Asset), config via `.env`.
2. **Auth** — AuthKit frontend integration + Firebase Admin SDK backend token verification middleware.
3. **Video upload + Whisper transcription** — Upload endpoint, model selection, Faster-Whisper pipeline, timestamped transcript storage.
4. **LLM generation** — Ollama integration; prompt engineering for JSON output (summary, mindmap, quiz, flashcards).
5. **Frontend views** — Jinja2 + HTMX + Tailwind; all views from spec (dashboard, course, video player, tabs); dark/light themes.
6. **Chat interface** — ChatGPT-style chat triggered by flashcard "Teach me real-world usage" button; persisted history.

## 5. Running Tests
```bash
pytest                    # unit tests (Whisper/Ollama mocked)
pytest -m slow           # integration tests (requires real Ollama + Whisper)
pytest --cov=app         # coverage report
```

## 6. Project Structure (Planned)
```
video-learning-app/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── config.py            # Settings via pydantic-settings
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models/              # SQLAlchemy ORM models
│   ├── routers/             # API route modules
│   ├── services/            # Business logic (transcription, llm, etc.)
│   ├── auth/                # Firebase token verification middleware
│   └── templates/           # Jinja2 HTML templates
├── static/                  # CSS, JS, images
├── uploads/                 # User video files (gitignored)
├── storage/                 # Generated assets (gitignored)
├── tests/                   # pytest test suite
├── doc/
│   ├── design.md
│   └── handover.md
├── .env.example
├── .gitignore
├── requirements.txt
└── Readme.md
```

## 7. Detailed Frontend Layout & UI Specifications

### Global Layout Structure
*   1 Sidebar + 1 Main Content Area.
*   **Desktop:** Fixed Sidebar (left, 250px). Main area takes remaining space.
*   **Mobile:** Sidebar hidden. Hamburger menu (☰) in top-left opens it as an overlay drawer.
*   **Top Header Bar:** Spans main area. Left: Hamburger/Logo. Center: Breadcrumbs. Right: User Profile/Logout.

### Sidebar Contents
*   Global Search Bar.
*   Navigation: `Dashboard` (Home), `My Courses` (Expandable list), `Chat History`.
*   Footer: User Avatar, Settings.

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