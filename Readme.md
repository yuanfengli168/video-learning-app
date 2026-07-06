# Video Learning App

An open-source, AI-powered web application designed to help users learn from online video classes. The app transcribes local video files, generates interactive learning materials (summaries, mindmaps, quizzes, flashcards), and provides a ChatGPT-style interface to chat with an AI about real-world applications of the concepts.

## Tech Stack
*   **Backend:** FastAPI (Python 3.11+)
*   **Frontend:** HTML, JavaScript, Tailwind CSS (Responsive for Desktop & iPhone)
*   **Database:** SQLAlchemy (SQLite for local MVP, PostgreSQL for cloud)
*   **AI Models:** Faster-Whisper (Transcription), Ollama (LLM processing via `glm-5.2:cloud`)
*   **Authentication:** [AuthKit](https://github.com/yuanfengli168/authkit)

## Features
*   **Course Hierarchy:** Organize videos into Courses and Sections (like Google Drive folders).
*   **AI Processing:** Automatically generate markdown summaries, HTML mindmaps, quizzes, and flashcards.
*   **Interactive Chat:** Click on a flashcard to open a chatroom where the AI teaches real-world usage (e.g., "How RAG works for OpenAI") and saves chat history.
*   **Transcript Viewer:** View transcripts with timestamps (MVP1) and click-to-seek video player integration (MVP2).

## License
This project is licensed under the Apache License 2.0.
EOF
