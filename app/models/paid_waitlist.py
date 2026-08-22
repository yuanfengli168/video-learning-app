"""PaidWaitlist model — emails of users interested in paid tier (v1.1).

Captures interest before Stripe is integrated. When payments go live,
admin exports these emails and sends a launch announcement via SendGrid /
Resend / etc.

Source field tracks where the signup came from so we can measure which
channels (web banner, friend referral, etc) drove signups.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PaidWaitlist(Base):
    """Email signup for the upcoming paid tier.

    Columns:
        id            UUID primary key
        email         User's email (UNIQUE so duplicate signups are ignored)
        message       Optional free-text from user ("why I want paid")
        source        Where the signup came from (web, friend, manual, etc)
        notified_at   When we emailed them about the launch (NULL = pending)
        created_at    First signup timestamp
    """

    __tablename__ = "paid_waitlist"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # RFC 5321 max email length is 254 chars
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Default 'web' so the most common case doesn't need explicit assignment
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PaidWaitlist id={self.id!r} email={self.email!r} "
            f"source={self.source!r}>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "message": self.message,
            "source": self.source,
            "notified_at": (
                self.notified_at.isoformat() if self.notified_at else None
            ),
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }
