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

    # ── YouTube Data API v3 (Day 2B) ──
    # Optional — if set, the admin upload endpoint enriches videos with
    # title, duration, and caption track listing. If empty, the endpoint
    # falls back to admin-provided title (Day 2A behavior).
    # Get yours: console.cloud.google.com → APIs & Services → Credentials
    # Quota: 10,000 units/day. videos.list costs 1 unit; captions.list costs 50.
    youtube_api_key: str = ""

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