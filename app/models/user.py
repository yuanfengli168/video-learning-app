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

from sqlalchemy import DateTime, Integer, String, Text, func
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
