# Video Learning App

![MVP2 Status](https://img.shields.io/badge/MVP2.0-shipped-brightgreen) ![Tests](https://img.shields.io/badge/tests-552%20passing-brightgreen) ![Coverage](https://img.shields.io/badge/coverage-92%25-brightgreen) ![Stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20Jinja2%20%7C%20SQLite%20%7C%20Ollama-blue) ![Branch](https://img.shields.io/badge/branch-main-yellow)

An open-source, AI-powered web application designed to help users learn from online video classes. The app transcribes local video files, generates interactive learning materials (summaries, mindmaps, quizzes, flashcards), and provides a ChatGPT-style interface to chat with an AI about real-world applications of the concepts.

> **MVP1 is finished** ([scorecard](doc/MVP1.0-successfullyFinished.md)). **MVP2.0 is finished** ([scorecard](doc/MVP2.0-successfullyFinished.md)) on `main` — 9 versions shipped (2.0.0 → 2.0.8 + same-day 2.0.8 amendment), 552/552 tests passing, 92% coverage. See [`doc/MVP2.0-Status.md`](doc/MVP2.0-Status.md) for the full per-version history, and [`doc/MVP2.1-all.md`](doc/MVP2.1-all.md) for what's next.

## Quick Commands
```
# First time only:
bash scripts/setup.sh                    # Install everything
# After downloading Firebase key from console:
bash scripts/setup_firebase_key.sh       # Move key to project root

# Every time you want to run the app:
bash scripts/start.sh                    # Foreground (auto-reload)
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs/server.log 2>&1 &
                                          # Background (survives terminal close)

bash scripts/stop.sh                     # Stop the server
bash scripts/status.sh                   # Check if it's running + smoke tests

# Every time you want to run tests:
bash scripts/test.sh                     # Run tests with coverage
```

See [`doc/HowToStart.md`](doc/HowToStart.md) for the full guide (troubleshooting, smoke tests, cheat sheet).

## Tech Stack
*   **Backend:** FastAPI (Python 3.14)
*   **Frontend:** Jinja2 templates + vanilla JS + Tailwind CSS (dark/light themes, responsive for Desktop & Mobile)
*   **Database:** SQLAlchemy 2.0 ORM with SQLite (local). PostgreSQL + Alembic migration target is queued for MVP2.0.1 wave 3 (#10).
*   **AI Models:** Faster-Whisper (Transcription, 4 model sizes), Ollama (LLM processing via `glm-5.2:cloud`)
*   **Authentication:** AuthKit Firebase login UI + Firebase Admin SDK (backend token verification) + httpOnly session cookies

## Features
*   **Course Hierarchy:** Organize videos into Courses → Sections → Videos (like Google Drive folders).
*   **Sidebar Search:** Filter the sidebar's course list in real time as you type — case-insensitive substring match, with a "No matches" placeholder.
*   **Chat History:** Visit `/chat-history` to see all your past flashcard chats, search them, continue any conversation, and delete ones you don't need.
*   **AI Processing:** Automatically generate markdown summaries, Markmap mindmaps, quizzes, flashcards, and per-topic timestamps from video transcripts.
*   **Responsive layout:** The video page works on mobile, tablet, and desktop. The main content uses `min-w-0` so flex children can shrink below their intrinsic width (preventing video overflow on small screens). The transcript header (title + controls) stacks vertically on mobile and sits side-by-side at sm+. Two-column split (60/40) activates at the `lg` breakpoint.
*   **Interactive Chat:** Click "Teach me real-world usage" on a flashcard to open a chatroom where the AI teaches real-world examples. Chat history is persisted.
*   **Transcript Viewer:** Timestamped transcript with click-to-seek video player integration, keyword search with live highlighting, and prev/next match navigation.
*   **Whisper Model Selection:** Choose between `tiny`, `base`, `small`, `medium` on the web UI. Models auto-download.
*   **Session-based Auth:** Firebase ID tokens are exchanged for httpOnly session cookies — no tokens in JavaScript.

## Quick Start
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # Fill in Firebase + Ollama config
uvicorn app.main:app --reload   # http://localhost:8000
```

### Prerequisites
*   **Ollama** running at `http://localhost:11434`. Install + start:
    ```bash
    brew install ollama
    ollama serve &              # or let scripts/start.sh auto-start it
    ollama pull glm-5.2:cloud   # the default LLM; ~2 GB download
    ```
*   **FFmpeg** for audio extraction (`brew install ffmpeg`)
*   **Firebase project** with Google + Email/Password auth enabled (see `doc/handover.md` for setup). **Important:** add `localhost` to your Firebase project's **Authentication → Settings → Authorized Domains** before testing Google sign-in, otherwise the popup will fail with a domain-not-authorized error.
*   **(Apple Silicon only, optional)** `pip install mlx-whisper` for the 5-10x faster MLX Whisper Large V3 Turbo backend. Without it, the app falls back to `faster-whisper` `base` (CPU, slower but still works). See `doc/MVP2.0-Status.md` §19 for the smart-pick details.

## Testing
```bash
bash scripts/test.sh        # full test suite with coverage
pytest                      # 552 unit tests (Whisper/Ollama mocked)
pytest --cov=app            # 92% coverage report
```

## Project Structure
```
app/
├── main.py              # FastAPI app entry
├── config.py            # Settings via pydantic-settings
├── database.py          # SQLAlchemy engine + session
├── models/              # ORM: Course, Section, Video, Asset, ChatSession, ChatMessage
├── routers/             # API routes: auth, session, courses, videos, generation, chat, frontend
├── services/            # Business logic: transcription, llm, chat, retry, transcript_export
├── auth/                # Firebase Admin SDK + session cookie helpers
├── middleware.py        # Security headers (CSP, HSTS, etc.)
├── middleware_session.py # 3-state session-cookie semantics (MVP2.0.6 logout fix)
├── jobs.py              # Job tracking (per-step timing, MVP2.0.4)
└── templates/           # Jinja2 HTML templates (base, dashboard, course, video, login, chat_history)
scripts/                 # setup.sh, setup_firebase_key.sh, start.sh, stop.sh, status.sh, test.sh, retry_failed_generate.py
tests/                   # 552 pytest tests (92% coverage) — see tests/ for the per-feature test files
doc/                     # design.md, handover.md, deployment.md, MVP1.0-successfullyFinished.md, MVP2.0-successfullyFinished.md, MVP2.0-Status.md, MVP2.0-first-designQuestions.md, MVP2.1-all.md, MVP3.0-Status.md
CHANGELOG.md             # version history (Keep a Changelog format, 2.0.0 → 2.0.8 + amendment)
SECURITY.md              # security policy + threat model + how to report vulns
```

## License
This project is licensed under the Apache License 2.0.
EOF
