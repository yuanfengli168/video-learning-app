"""UploadSession model — chunked-upload staging state (13a, 2026-09-21).

One row per chunked upload in flight. The FILESYSTEM is the source of
truth for which chunks have arrived (staging dir = one subdirectory per
session under upload_dir/sessions/<session_id>/, one file per chunk);
this row holds identity + metadata + the sweep bookkeeping.

Design provenance (doc/limits-registry.md §3a):
  - Filesystem-as-truth: the resume bitmap is a directory listing —
    a crash leaves whatever chunks landed and nothing can drift
    (the 9/20 orphan-file lesson, systematized).
  - last_activity_at updates on EVERY chunk receipt — the sweeper's
    1h TTL keys on it (abandonment = silence, not session age).
  - status transitions: active → completed (assembly done, staging
    dir consumed) | cancelled (user DELETE, or swept by the 1h TTL).

This table does NOT hold chunk content or chunk inventory — only
declared metadata. Never trust this row over the staging directory.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UploadSession(Base):
    """A chunked upload in flight (one active session per user)."""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="session uuid (also the staging dir name)"
    )
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="owner uid — every endpoint re-checks ownership",
    )
    section_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
        comment="target section (ownership-validated at init)",
    )
    original_filename: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="sanitized display name"
    )
    # The file's EXTENSION (validated against ALLOWED_EXTENSIONS at init).
    # Kept separately so assembly can name the final file without
    # re-parsing the display filename.
    extension: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="'.mp4' etc — server-validated allowlist"
    )
    declared_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="total bytes the client declared — quota reserves this; complete() verifies it",
    )
    chunk_size: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="bytes per chunk (last chunk may be smaller)"
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="expected chunk count = ceil(declared/chunk_size)"
    )
    # active | completed | cancelled (registry §3a). The chunk endpoint
    # rejects non-active sessions with 409 — the explicit-race answer to
    # the claim-vs-chunk window in the sweeper design.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True
    )
    # The user's whisper model choice at init time, replayed at
    # complete() when the Video row is created (the legacy upload path
    # stamps this at upload; chunked sessions preserve parity).
    whisper_model: Mapped[str | None] = mapped_column(String(64), default=None)
    # Optional language lock (registry: same option as legacy path).
    language: Mapped[str | None] = mapped_column(String(8), default=None)

    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.utcnow(),
        index=True,
        comment="updated on EVERY chunk receipt — the sweeper's TTL clock",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<UploadSession id={self.id[:8]}… user={self.user_id[:8]}… "
            f"status={self.status} {self.declared_size}B "
            f"({self.total_chunks}×{self.chunk_size}B)>"
        )