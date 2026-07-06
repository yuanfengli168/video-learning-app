# Video Learning App

![MVP1 Status](https://img.shields.io/badge/MVP1-shipped-brightgreen) ![Tests](https://img.shields.io/badge/tests-218%20passing-brightgreen) ![Coverage](https://img.shields.io/badge/coverage-96%25-brightgreen) ![Stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20Jinja2%20%7C%20SQLite%20%%7C%20Ollama-blue)

An open-source, AI-powered web application designed to help users learn from online video classes. The app transcribes local video files, generates interactive learning materials (summaries, mindmaps, quizzes, flashcards), and provides a ChatGPT-style interface to chat with an AI about real-world applications of the concepts.

> **MVP1 is finished.** See [`doc/MVP1.0-successfullyFinished.md`](doc/MVP1.0-successfullyFinished.md) for the full scorecard (architecture checklist, data model coverage, AI pipeline verification, feature scorecard, and bug-fix history).

## Quick Commands
```
# First time only:
bash scripts/setup.sh                    # Install everything
# After downloading Firebase key from console:
bash scripts/setup_firebase_key.sh       # Move key to project root

# Every time you want to run the app:
bash scripts/start.sh                    # Start Ollama + app

# Every time you want to run tests:
bash scripts/test.sh                     # Run tests with coverage
```

## Tech Stack
*   **Backend:** FastAPI (Python 3.11+)
*   **Frontend:** Jinja2 templates + HTMX + Tailwind CSS (dark/light themes, responsive for Desktop & Mobile)
*   **Database:** SQLAlchemy ORM (SQLite for local MVP1, PostgreSQL for MVP2)
*   **AI Models:** Faster-Whisper (Transcription), Ollama (LLM processing via `glm-5.2:cloud`)
*   **Authentication:** [AuthKit](https://github.com/yuanfengli168/authkit) (frontend Firebase Auth UI) + Firebase Admin SDK (backend token verification)

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
*   **Ollama** running at `http://localhost:11434` (`ollama pull glm-5.2:cloud`)
*   **FFmpeg** for audio extraction (`brew install ffmpeg`)
*   **Firebase project** with Google + Email/Password auth enabled (see `doc/handover.md` for setup)

## Testing
```bash
bash scripts/test.sh        # full test suite with coverage
pytest                      # 218 unit tests (Whisper/Ollama mocked)
pytest --cov=app            # 96% coverage report
```

## Project Structure
```
app/
├── main.py              # FastAPI app entry
├── config.py            # Settings via pydantic-settings
├── database.py          # SQLAlchemy engine + session
├── models/              # ORM: Course, Section, Video, Asset, ChatSession, ChatMessage
├── routers/             # API routes: auth, session, courses, videos, generation, chat, frontend
├── services/            # Business logic: transcription, llm, chat
├── auth/                # Firebase Admin SDK + session cookie helpers
└── templates/           # Jinja2 HTML templates (base, dashboard, course, video, login)
scripts/                 # setup.sh, setup_firebase_key.sh, start.sh, test.sh
tests/                   # 218 pytest tests (96% coverage)
doc/                     # design.md, handover.md, deployment.md, MVP1.0-successfullyFinished.md
```

## License
This project is licensed under the Apache License 2.0.
EOF
