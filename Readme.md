# Video Learning App

An open-source, AI-powered web application designed to help users learn from online video classes. The app transcribes local video files, generates interactive learning materials (summaries, mindmaps, quizzes, flashcards), and provides a ChatGPT-style interface to chat with an AI about real-world applications of the concepts.

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
*   **AI Processing:** Automatically generate markdown summaries, Markmap mindmaps, quizzes, and flashcards from video transcripts.
*   **Interactive Chat:** Click "Teach me real-world usage" on a flashcard to open a chatroom where the AI teaches real-world examples. Chat history is persisted.
*   **Transcript Viewer:** Timestamped transcript with click-to-seek video player integration and keyword search.
*   **Whisper Model Selection:** Choose between `base`, `small`, `medium` (and more) on the web UI. Models auto-download.

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
pytest                    # 140 unit tests (Whisper/Ollama mocked)
pytest --cov=app          # 96% coverage report
```

## Project Structure
```
app/
├── main.py              # FastAPI app entry
├── config.py            # Settings via pydantic-settings
├── database.py          # SQLAlchemy engine + session
├── models/              # ORM: Course, Section, Video, Asset, ChatSession, ChatMessage
├── routers/             # API routes: auth, courses, videos, generation, chat, frontend
├── services/            # Business logic: transcription, llm, chat
├── auth/                # Firebase Admin SDK token verification
└── templates/           # Jinja2 HTML templates (base, dashboard, course, video, login)
tests/                   # 140 pytest tests (96% coverage)
doc/                     # Design + handover docs
```

## License
This project is licensed under the Apache License 2.0.
EOF
