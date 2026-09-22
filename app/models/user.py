"""User model — links Firebase UID to a role + tracking metadata.

See doc/mvp2-roles-and-access.md for the full design (UserRole enum,
capability map, manual promotion flow, etc.).

This model is intentionally separate from Firebase Auth — Firebase is the
identity provider (issues tokens), this table is our internal authorization
layer (decides what a user can do).

Security note (2026-08-22, jacky.li): role is NEVER trusted from Firebase
claims. Every request looks up role from this table.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """Internal user record, keyed by Firebase UID.

    Auto-created on first authenticated request (see app/auth/admin.py:
    ensure_user_row). Idempotent INSERT OR IGNORE so concurrent first-
    login requests don't race.

    Columns:
        user_id      Firebase UID (primary key)
        email        Firebase email (NULL allowed for privacy)
        role         UserRole enum int (0=ADMIN, 1=PAID, 2=FREE)
        notes        Admin notes (e.g. "early tester", "founder")
        created_at   First-seen timestamp
        updated_at   Last-seen timestamp (auto-updated)
    """

    __tablename__ = "users"

    # Firebase UIDs are strings (e.g. "abc123XYZ"). Up to 128 chars
    # accommodates Firebase's maximum UID length.
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    # Default 2 (FREE) so a brand-new row is always least-privileged.
    # Admin promotion requires explicit UPDATE — no self-service path.
    role: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Upload limit overrides (13a, 2026-09-21 — registry §1/§2) ──
    # NULL = use the tier default (env var). A set value BEATS the tier
    # default — this IS the paid-add-on infrastructure: "they paid more
    # → flip their override" (one SQL update via the flip-kit, no code).
    # Kept on users (not a separate table) because the resolver reads
    # the user row anyway on every gated request.
    max_file_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None,
        comment="per-user max upload file size override (NULL = tier default)",
    )
    storage_quota_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None,
        comment="per-user storage quota override (NULL = tier default)",
    )
    # ── Model preference (2026-09-22, doc/model-preference-design.md) ──
    # The user's ollama-model choice. NULL = tier default (PAID gets
    # LLM_MODEL_PAID_DEFAULT, ADMIN gets LLM_MODEL_ADMIN_DEFAULT).
    # Only the ADMIN's row is written today (via /admin/settings); when
    # MVP3 exposes model choice to PAID it's this same column + the
    # same resolver + one more capability-gated page. The value is
    # validated against LLM_MODEL_CATALOG at write time AND re-checked
    # at read time (an override removed from the catalog falls back to
    # the tier default — graceful retirement, no migration).
    llm_model_pref: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None,
        comment="per-user ollama model override (NULL = tier default)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<User user_id={self.user_id!r} email={self.email!r} "
            f"role={self.role}>"
        )

    def to_dict(self) -> dict:
        """Safe serialization (no secrets, just public-facing fields)."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "role": self.role,
            "notes": self.notes,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }
