"""Application configuration via pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── App ──
    app_name: str = "Video Learning App"
    debug: bool = True

    # ── Database ──
    database_url: str = "sqlite:///./video_learning.db"

    # ── Ollama ──
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "glm-5.2:cloud"

    # ── Firebase (frontend config) ──
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""
    firebase_storage_bucket: str = ""
    firebase_messaging_sender_id: str = ""
    firebase_app_id: str = ""

    # ── Firebase Admin SDK (backend) ──
    firebase_service_account_key_path: str = "./firebase-service-account.json"

    # ── Storage ──
    upload_dir: str = "./uploads"
    storage_dir: str = "./storage"

    # ── Language detection (MVP3.0 #2b, anti-drift) ────────────────────────
    # When auto-detecting the primary language of a video, sample the
    # first N windows of 30s (so N=20 = 10 min of audio) and pick the
    # language with the highest total probability across windows that
    # have actual speech (no_speech_prob < 0.5). The cost is ~6-12s for
    # 10 min of audio on M1 Max mlx-whisper (very cheap relative to a
    # full 2.5h transcribe). Lower = faster but less robust to intros
    # (songs, silence); higher = more accurate but slower.
    language_detect_sample_windows: int = 20
    # Threshold for "is this window real speech?" Windows above this
    # are skipped during the language tally. Whisper's default is 0.6;
    # we use 0.5 to be slightly more inclusive (Mandarin with quiet
    # speakers can hover around 0.55).
    language_detect_speech_threshold: float = 0.5

    # ── MVP0.2: course materials (PDF / .md / .txt / .zip uploads) ──
    # Per-user storage cap for uploaded materials. Tested with small PDFs
    # (~2 MB) and .zip of small code repos (~500 KB); 200 MB = ~100
    # typical PDFs, plenty for a personal study tool.
    materials_max_total_bytes_per_user: int = 200 * 1024 * 1024  # 200 MB
    # Single-file upload limit. 50 MB covers most textbooks and code
    # repos; anything bigger should be split or done via cloud sync (later).
    materials_max_file_bytes: int = 50 * 1024 * 1024  # 50 MB
    # Default visibility scope when uploading from a section page.
    # "section" = visible to all videos in the section (default for MVP0.2).
    # "course"  = visible to all videos in the course (deferred — config
    # support only, no UI in MVP0.2).
    materials_default_scope: str = "section"

    # ── MVP0.2 followup: OCR for image-only PDFs (jsPDF, scans) ──
    # When pypdf returns 0 chars (the PDF has no text layer), the
    # extractor tries these in order until one produces text:
    #   1. macOS Vision framework (VNRecognizeTextRequest) — preinstalled
    #      on every Mac running macOS 12.0+, runs on the Neural Engine,
    #      ~1-3s per page, excellent Chinese accuracy. Skipped on
    #      non-macOS hosts (Linux deploys etc).
    #   2. Ollama vision model (llava:13b / qwen2-vl / etc) — if any
    #      model with 'vision' capability is already pulled on the
    #      Ollama server we talk to. Uses local GPU via Ollama.
    #   3. Tesseract via Homebrew — `brew install tesseract tesseract-lang`.
    #      Slowest (CPU only) but works for very large docs and as the
    #      final fallback when nothing else is available.
    materials_ocr_enabled: bool = True
    # macOS Vision is fast; only switch to Tesseract above this many
    # pages (Vision still works on big docs but quality + time drops).
    materials_ocr_macos_vision_max_pages: int = 50
    # Ollama vision is slower than macOS Vision but tolerates more pages
    # and gives better accuracy on dense layouts; use it as primary when
    # Vision is unavailable (Linux deploy).
    materials_ocr_ollama_vision_model: str = "llava:13b"
    # Tesseract languages: "chi_sim+eng" covers English + Simplified
    # Chinese (the two main languages our users upload). Add "chi_tra"
    # for Traditional if needed.
    materials_ocr_tesseract_lang: str = "chi_sim+eng"
    # Wall-clock cap on the entire OCR chain. PDFs that don't finish in
    # this many seconds get marked failed. Generous because Tesseract
    # on a 200-page textbook is slow on CPU.
    materials_ocr_timeout_seconds: int = 600

    # ── Ollama request timeouts (MVP0.2 followup) ──
    # Historically hardcoded to 120s in `chat_with_ollama` and
    # `_call_ollama`. That value is too tight for two reasons:
    #   1. The video-scope chat puts the user's full transcript
    #      (up to 200K chars) + summary + mindmap + quiz + selected
    #      materials into the system prompt. With 5 selected materials
    #      we've measured the prompt at 220K chars (~110K tokens for
    #      Chinese). On Mac M1 Max with `glm-5.2:cloud`, prefill alone
    #      takes ~50s and generation takes another 60-150s.
    #   2. The pocket tutor has a `PROMPT_CHAR_LIMIT = 200_000` fallback
    #      for the chunk-generation prompt, but the minimal fallback
    #      still carries the materials section (~200K chars on its own).
    #
    # We bump the default to 600s (10 min) for chunk-generation and
    # 300s for chat (which is mostly prefill then small generation).
    # Both are configurable so users on a slower CPU can shorten them.
    ollama_chat_timeout_seconds: float = 300.0
    ollama_pocket_tutor_timeout_seconds: float = 600.0
    ollama_pocket_grading_timeout_seconds: float = 60.0

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()