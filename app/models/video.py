"""Video model — an individual class file within a Section."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
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
    # ── Background job state (MVP1 progress bar + ETA) ──────────────────────
    # 'transcribe' or 'generate' — the latest job of that type for this video.
    # Stored as a JSON string of the Job dict from app/jobs.py. Nullable
    # when no job has ever run, or after a job completes and is cleared by
    # the next /status poll.
    last_transcribe_job: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_generate_job: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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