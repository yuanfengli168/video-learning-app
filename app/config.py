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

    # ── LiteLLM multi-provider setup (Day 4) ─────────────────────────
    # Per-tier provider chains. The chains determine which providers
    # `call_llm_with_fallback()` tries for a given user role.
    #
    # Captured 2026-08-24 from user input:
    #   - FREE users → Groq only (less powerful but "free as always").
    #     Groq failures are surfaced as a warning message; not retried.
    #   - PAID/ADMIN → Ollama first (powerful), OpenAI fallback (when
    #     Ollama quota near cap). Groq is NEVER used for paid users
    #     because it's less powerful.
    #
    # When a chain is empty or the only listed provider fails, the
    # caller gets a structured warning dict (not a 500). See
    # `app/services/llm_providers.py` for the fallback logic.
    #
    # Format: comma-separated provider names. LiteLLM model strings
    # are built as "<provider>/<model>" automatically.
    llm_provider_chain_free: str = "groq"
    llm_provider_chain_paid: str = "ollama,openai"
    llm_provider_chain_admin: str = "ollama,openai"

    # Per-provider default models. Picked for free-tier value (groq)
    # and quality (ollama glm-5.2, openai gpt-4o-mini).
    llm_model_groq: str = "llama-3.3-70b-versatile"
    llm_model_ollama: str = "glm-5.2:cloud"
    llm_model_openai: str = "gpt-4o-mini"

    # ── Ollama Pro quota (Day 4) ─────────────────────────────────────
    # User's $20/month Ollama Pro account: 800 req/5h, 3000 req/week.
    # The quota tracker in `app/services/llm_quota.py` records every
    # Ollama call and signals when we're at `quota_alert_pct` of either
    # limit — at which point `call_llm_with_fallback()` auto-skips Ollama
    # and goes straight to OpenAI (the paid fallback).
    #
    # Why 90%: leaves a small safety margin in case Ollama's sliding
    # window catches up to us. Adjust lower (e.g. 0.8) if you're
    # getting 429s before the alert fires.
    ollama_5h_request_limit: int = 800
    ollama_weekly_request_limit: int = 3000
    ollama_quota_alert_pct: float = 0.9

    # ── Per-user rate limiting (Day 4) ────────────────────────────────
    # Tier-based: FREE is strictest, ADMIN most permissive. These are
    # LLM CALL counts (generate_materials, chat, regenerate, etc.) not
    # general HTTP requests. The limiter is in-memory; resets on
    # server restart (acceptable for MVP — see Day 4 plan).
    #
    # Math: with PAID at 15/min × 200/day, one Ollama Pro account
    # (800 req/5h, 3000 req/week) supports ~16 paid users at peak.
    rate_limit_free_per_min: int = 5
    rate_limit_free_per_day: int = 30
    rate_limit_paid_per_min: int = 15
    rate_limit_paid_per_day: int = 200
    rate_limit_admin_per_min: int = 60
    rate_limit_admin_per_day: int = 1000

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

    # ── LiteLLM provider chain helpers (Day 4) ───────────────────────
    def get_provider_chain(self, user_role: int) -> list[str]:
        """Return the ordered provider list for a user's role.

        Args:
            user_role: UserRole enum value (0=ADMIN, 1=PAID, 2=FREE).
                Falls back to FREE chain for unknown values.

        Returns:
            List of provider names in fallback order. Always non-empty
            (defaults to FREE chain if config is broken).
        """
        from app.auth.roles import UserRole

        chain_str = {
            UserRole.ADMIN: self.llm_provider_chain_admin,
            UserRole.PAID: self.llm_provider_chain_paid,
            UserRole.FREE: self.llm_provider_chain_free,
        }.get(UserRole(user_role) if user_role in (0, 1, 2) else None,
              self.llm_provider_chain_free)
        return [p.strip() for p in chain_str.split(",") if p.strip()]

    def get_model_for_provider(self, provider: str) -> str:
        """Return the LiteLLM model name for a provider.

        Examples:
            get_model_for_provider("groq") -> "llama-3.3-70b-versatile"
            get_model_for_provider("ollama") -> "glm-5.2:cloud"
            get_model_for_provider("openai") -> "gpt-4o-mini"
        """
        return {
            "groq": self.llm_model_groq,
            "ollama": self.llm_model_ollama,
            "openai": self.llm_model_openai,
        }.get(provider, "")

    def get_rate_limit_per_min(self, user_role: int) -> int:
        """LLM-call-per-minute cap for the given role."""
        from app.auth.roles import UserRole

        return {
            UserRole.ADMIN: self.rate_limit_admin_per_min,
            UserRole.PAID: self.rate_limit_paid_per_min,
            UserRole.FREE: self.rate_limit_free_per_min,
        }.get(UserRole(user_role) if user_role in (0, 1, 2) else None,
              self.rate_limit_free_per_min)

    def get_rate_limit_per_day(self, user_role: int) -> int:
        """LLM-call-per-day cap for the given role."""
        from app.auth.roles import UserRole

        return {
            UserRole.ADMIN: self.rate_limit_admin_per_day,
            UserRole.PAID: self.rate_limit_paid_per_day,
            UserRole.FREE: self.rate_limit_free_per_day,
        }.get(UserRole(user_role) if user_role in (0, 1, 2) else None,
              self.rate_limit_free_per_day)


settings = Settings()