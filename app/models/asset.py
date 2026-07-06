"""Asset model — generated materials linked to a Video."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Asset(Base):
    """Generated materials (Summary, Flashcards, Quiz, Mindmap, Transcript).

    Asset types:
        - summary     → Markdown text in `content`
        - transcript  → JSON list of {start, end, text} in `content`
        - flashcards  → JSON list of {term, definition} in `content`
        - quiz        → JSON list of {question, options, answer} in `content`
        - mindmap     → Markmap-compatible markdown in `content`
    """

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    video_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    # Type of asset: summary, transcript, flashcards, quiz, mindmap
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Content stored as text (JSON for structured assets, markdown for summary/mindmap)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    video: Mapped["Video"] = relationship("Video", back_populates="assets")