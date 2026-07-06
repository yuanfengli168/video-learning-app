"""Video model — an individual class file within a Section."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Video(Base):
    """The individual class file."""

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Integer, default=0)  # seconds
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    section_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    # Processing status: pending, transcribing, generating, ready, error
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # Whisper model used for transcription
    whisper_model: Mapped[str] = mapped_column(String(32), default="base")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    section: Mapped["Section"] = relationship("Section", back_populates="videos")
    assets: Mapped[list["Asset"]] = relationship(
        "Asset",
        back_populates="video",
        cascade="all, delete-orphan",
    )