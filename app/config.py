"""Application configuration via pydantic-settings."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── App ──
    # 2026-09-18 rebrand: capysmart.com purchased; "Video Learning App"
    # stays as the subtitle/tagline ("Turn any lecture into a study
    # kit"). The name never travels without the tagline — see the
    # landing page hero and login hero for the pairing.
    app_name: str = "CapySmart"
    # Subtitle shown alongside/under the brand where a plain string
    # can't carry both. Empty string = hidden (e.g. compact headers).
    app_subtitle: str = "Video Learning App"
    # Public community link (Discord). Empty string = the sidebar link
    # is hidden entirely. Store the invite URL here (not in templates)
    # so rotating the invite (or switching to a vanity URL like
    # discord.gg/capysmart later) is a one-line .env change + restart,
    # never a code deployment.
    community_invite_url: str = ""
    debug: bool = True
    # 2026-09-12 (go-live prep, public-repo-readiness.md rec #1):
    # Set the fb_token cookie's Secure flag. TRUE in production (HTTPS
    # via Cloudflare Tunnel); FALSE keeps local-dev http://localhost
    # logins working (browsers refuse Secure cookies over plain http).
    # Flip via .env at ops time — no code change needed per environment.
    cookie_secure: bool = False

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

    # ── Chunked uploads (13a, 2026-09-21 — registry doc/limits-registry.md §1–3a) ──
    # Per-file upload caps by tier. Env-driven so raising PAID from 1GB
    # to 2GB/4GB is a config change, not code surgery. Per-user overrides
    # (users.max_file_bytes) beat these tier defaults — the paid-add-on
    # infrastructure ("they paid more → flip their override").
    upload_max_file_paid_gb: float = 1.0      # UPLOAD_MAX_FILE_PAID_GB
    upload_max_file_admin_gb: float = 20.0    # UPLOAD_MAX_FILE_ADMIN_GB

    # Total storage quotas by tier (registry §2). ADMIN 100GB is a
    # soft/documented cap — the owner holds the override column.
    storage_quota_paid_gb: float = 25.0      # STORAGE_QUOTA_PAID_GB
    storage_quota_admin_gb: float = 100.0    # STORAGE_QUOTA_ADMIN_GB

    # Chunk transport (registry §3): 32MB is under the 100MB edge cap
    # and completes in <~30s even on weak uplinks (a 1GB file = 32
    # chunks).
    upload_chunk_size_mb: int = 32           # UPLOAD_CHUNK_SIZE_MB

    # Abandoned-session sweeper TTL (registry §3a, Round 2 ratified):
    # 1h of NO chunk activity → staging deleted + row 'cancelled' +
    # the interrupted-upload banner on the user's next upload-page
    # visit. "Effectively immediate in human terms" while barely
    # surviving a lunch-break lid-close.
    upload_session_ttl_hours: float = 1.0    # UPLOAD_SESSION_TTL_HOURS

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
    #
    # Why groq/compound-mini (not groq/compound which Day 5 hotfix
    # originally picked):
    # Groq deprecated all direct Llama models in Aug 2026. The
    # current free text+json options are:
    #   - groq/compound       (router) — picks sub-models, 413s
    #                           consistently for our 3k-token system
    #                           prompts because the sub-model it picks
    #                           has a small request limit
    #   - groq/compound-mini  (router) — fast, handles our request size
    #                           consistently, 131k ctx
    #   - allam-2-7b          — 4k ctx, too small for transcripts
    # We pick groq/compound-mini: free, 131k ctx, works for our
    # 3k-token system prompts + 30k-token transcripts. On 429
    # (sub-model rate-limited) we surface the error to the user
    # rather than fall back to a paid model — keeps the free tier
    # strictly $0. See doc/mvp2-final-go-live-plan.md §Groq
    # strategy for full reasoning.
    llm_model_groq: str = "groq/compound-mini"
    llm_model_ollama: str = "glm-5.2:cloud"
    llm_model_openai: str = "gpt-4o-mini"

    # ── Model preference system (2026-09-22, doc/model-preference-design.md) ──
    # Catalog-driven model selection: PAID users get LLM_MODEL_PAID_DEFAULT
    # (owner-set, no choice for now), ADMIN picks via /admin/settings.
    # Adding glm-5.4 next month = `ollama pull` + append to the catalog +
    # restart — ZERO code change (the settings page + the resolver both
    # read this string). Resolution (the proven 13d-lite two-layer pattern):
    #   user override (users.llm_model_pref, if set AND in catalog)
    #     → tier default (PAID/ADMIN env below)
    #     → legacy fallback (llm_model_ollama above)
    # FREE is untouched (groq chain — no ollama branch at all).
    llm_model_catalog: str = "glm-5.2:cloud,minimax-m3:cloud,glm-5.3:cloud"
    llm_model_paid_default: str = "minimax-m3:cloud"
    llm_model_admin_default: str = "glm-5.2:cloud"

    def get_model_catalog(self) -> list[str]:
        """The selectable model list, parsed from LLM_MODEL_CATALOG.

        Comma-separated, whitespace-stripped, empty entries dropped —
        forgiving for hand-edited .env values.
        """
        return [
            m.strip() for m in self.llm_model_catalog.split(",") if m.strip()
        ]

    def get_tier_default_model(self, user_role: int) -> str:
        """Tier default model (the layer between override and legacy).

        Unknown roles get the FREE-adjacent behavior of the legacy
        llm_model_ollama — the resolver's fail-safe (a role we can't
        confirm never gets the paid-tier default silently).
        """
        if user_role == 0:  # ADMIN
            return self.llm_model_admin_default
        if user_role == 1:  # PAID
            return self.llm_model_paid_default
        return self.llm_model_ollama

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
    # Math (Day 5 hotfix): Groq's free tier is 250 req/day TOTAL across
    # the API key (shared by all FREE users). Lowered FREE from 30→15/day
    # so 10 free users × 15 = 150/day peak, leaving 100 req/day headroom
    # under Groq's global cap. Raise again only after we add a per-key
    # Groq quota tracker (see doc/mvp2-final-go-live-plan.md §Groq tracker).
    rate_limit_free_per_min: int = 5
    rate_limit_free_per_day: int = 15
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
            get_model_for_provider("groq") -> "groq/compound-mini"
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