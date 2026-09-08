"""Course model — top-level container in the hierarchy."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Course(Base):
    """The overarching topic (e.g., 'Machine Learning')."""

    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # 2026-09-08 channels: NULL = personal course (today's behavior,
    #   ownership checks key off user_id). NOT NULL = channel-owned
    #   catalog playlist — it renders under /channel/{slug} and the
    #   channel is its home (user_id becomes provenance metadata).
    #   Access/visibility stays per-Video (channel-level visibility
    #   deferred by product decision 2026-09-08).
    channel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("channels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    channel: Mapped["Channel | None"] = relationship(
        "Channel", back_populates="playlists"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    sections: Mapped[list["Section"]] = relationship(
        "Section",
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Section.order_index",
    )