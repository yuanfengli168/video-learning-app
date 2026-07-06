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
*   **LLM:** Ollama running locally at `http://localhost:11434` with the `glm-5.2:cloud` model. Calls use `temperature: 0` and a fixed `seed: 42` for deterministic output.
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
*   `Asset`: Generated materials (Summary, Flashcards, Quiz, Mindmap, Transcript, Topic Timestamps) linked to a Video. `topic_timestamps` is a list of `{topic, start, end}` objects used by the clickable mindmap.
*   `ChatSession`: A chat session triggered by a flashcard's "Teach me real-world usage" button. Linked to a Video and User.
*   `ChatMessage`: Individual messages (user/assistant) within a ChatSession.

## 4. AI Processing Pipeline
1.  **Input:** User selects a local video file.
2.  **Transcription:** Faster-Whisper extracts audio (via ffmpeg) and generates a per-sentence timestamped transcript.
3.  **LLM Generation:** Transcript is sent to Ollama (default model: `glm-5.2:cloud`) with **`temperature: 0` and a fixed `seed: 42`** to guarantee deterministic output — re-generating the same transcript produces the same materials every time. Prompt engineering forces a JSON response containing:
    *   Succinct markdown summary.
    *   Mindmap data (rendered via Markmap).
    *   Quiz questions and answers.
    *   Flashcard terms and definitions.
    *   `topic_timestamps` — for each major mindmap topic, the start/end seconds (derived from the transcript) where that topic is discussed. Used by the clickable mindmap to navigate the video. Typically only parent / level-1 branches are enumerated by the LLM; the frontend walks up the mindmap tree (see §5) to find timestamps for leaf nodes too.
4.  **Storage:** Saved to the local database and file system.

## 5. Features Scope
*   **Chat Interface:** A ChatGPT-like UI. Flashcards have a "Teach me real-world usage" button. Clicking it creates a ChatSession with a specific system prompt to teach real-world examples. Chat history is persisted in the database (ChatSession → ChatMessage).
*   **Chat History Page:** A dedicated `/chat-history` page with a searchable list of all the user's past chat sessions, a messages panel for the selected session, and an inline composer to continue any conversation. Per-session delete button.
*   **Transcript Viewer:** A Coursera-style UI where clicking a timestamp (e.g., 00:05:32) seeks the video player to that exact second. Includes a search bar with live highlighting, match count, and prev/next navigation.
*   **Clickable Mindmap:** Markmap nodes are interactive. Clicking a node jumps the video to that topic's start time, displays a topic banner (name + time range, or `Leaf → Parent` when ancestor-matching is used), and highlights the matching transcript lines. Works in both inline and fullscreen mindmap views. If neither the node nor any of its ancestors has a timestamp, a non-blocking toast is shown in the bottom-right corner (no alert dialog).
*   **Mindmap Controls:** Inline and fullscreen views support zoom (+/–), fit-to-screen, drag-to-pan, scroll-to-zoom, and Ctrl+0 to reset. All nodes display a pointer cursor and a hover tooltip (`▶ Click to watch this part of the video`).
*   **Session-based Auth:** AuthKit issues a Firebase ID token on login; the frontend exchanges it for an httpOnly session cookie via `POST /api/auth/session`. All subsequent API calls authenticate via the cookie — the JS code never needs to handle tokens.
*   **Sidebar Search:** The sidebar's course list is searchable in real time as the user types — case-insensitive substring match against the course title, with a "No matches" placeholder when nothing matches.
*   **Video Downloader (Future):** `yt-dlp` integration to download videos directly from URLs, documented for future implementation.
EOF
