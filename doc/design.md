# System Design Document

## 1. Overview
The application is designed to scale from a local single-user environment (MVP1) to a cloud-deployed, multi-user paid service (MVP2). The architecture prioritizes decoupling and clean interfaces to ensure seamless scaling.

## 2. Architecture

### MVP1 (Local Foundation)
*   **API:** FastAPI running locally.
*   **Frontend:** Jinja2 server-rendered templates + HTMX for partial updates (SPA-like feel without a JS framework). Tailwind CSS for styling with dark/light theme support.
*   **Database:** SQLite (using SQLAlchemy ORM) for easy local setup. Schema managed via `Base.metadata.create_all()` (Alembic deferred to MVP2).
*   **Storage:** Local file system for video uploads (`uploads/`, gitignored) and generated assets (`storage/`, gitignored).
*   **Processing:** Synchronous execution for transcription and LLM generation (acceptable for single-user local use).
*   **Authentication:** [yuanfengli168/authkit](https://github.com/yuanfengli168/authkit) on the frontend (Firebase Auth UI — Google + email/password). Backend verifies Firebase ID tokens via Firebase Admin SDK as JWT middleware.
*   **Transcription:** Faster-Whisper with model selectable on the web UI (`base`, `small`, `medium`). Models auto-download on first use.
*   **LLM:** Ollama running locally at `http://localhost:11434` with the `glm-5.2:cloud` model.
*   **Testing:** pytest + pytest-asyncio + httpx for API tests. Whisper and Ollama are mocked in unit tests; integration tests run against real services (marked as slow). Target ≥90% coverage on backend logic.
*   **Dependency Management:** `requirements.txt` (no Poetry for MVP1).

### MVP2 (Cloud Scalable)
*   **Deployment:** Docker & Kubernetes (K8s).
*   **Database:** PostgreSQL.
*   **Storage:** Amazon S3 (or MinIO) for video files, transcripts, and markdown.
*   **Task Queue:** Celery + Redis to handle long-running video processing jobs asynchronously without blocking the API.
*   **Auth/Payments:** OAuth2 (Google/GitHub) integration and Stripe for paid memberships.

## 3. Data Model (Hierarchy)
To handle large classes (100+ videos) neatly, the data is structured hierarchically:
*   `Course`: The overarching topic (e.g., "Machine Learning").
*   `Section`: A module or week (e.g., "Week 1: Neural Networks").
*   `Video`: The individual class file.
*   `Asset`: Generated materials (Summary, Flashcards, Quiz, Chat) linked to a Video.

## 4. AI Processing Pipeline
1.  **Input:** User selects a local video file.
2.  **Transcription:** Faster-Whisper extracts audio (via ffmpeg) and generates a per-sentence timestamped transcript.
3.  **LLM Generation:** Transcript is sent to Ollama (default model: `glm-5.2:cloud`). Prompt engineering forces a JSON response containing:
    *   Succinct markdown summary.
    *   Mindmap data (rendered via Markmap).
    *   Quiz questions and answers.
    *   Flashcard terms and definitions.
4.  **Storage:** Saved to the local database and file system.

## 5. Features Scope
*   **Chat Interface:** A ChatGPT-like UI. Flashcards have a "Real World Usage" button. Clicking it initializes an Ollama chat with a specific system prompt to teach real-world examples. Chat history is persisted in the database.
*   **Transcript Viewer (MVP2):** A Coursera-style UI where clicking a timestamp (e.g., 00:05:32) seeks the video player to that exact second. Includes a search bar to find keywords in the transcript.
*   **Video Downloader (Future):** `yt-dlp` integration to download videos directly from URLs, documented for future implementation.
EOF
