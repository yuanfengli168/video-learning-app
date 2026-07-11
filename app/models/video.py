"""Video model — an individual class file within a Section."""

import re
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# Natural-sort key extractor: matches a leading number, e.g.:
#   "1.-AI大模型..."     → (1,  ...)
#   "10 - Foo bar"        → (10, ...)
#   "Lesson 3: intro"     → (None, ...) — no leading number, fall back to alpha
#   "2_机器学习"          → (2, ...)
#   "ep5.mkv"             → (None, ...) — no leading number
#
# The first tuple element is the leading number (or None), the second is the
# raw lower-cased title for a stable secondary sort. This must match the JS
# implementation in app/templates/course.html (sortVideos) so server and
# client agree on the same order.
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)\s*[-._:：、\s]")


def natural_sort_key(title: str) -> tuple[int, str]:
    """Return a sort key for a video title that gives 'natural' ordering.

    Titles with a leading number (e.g. '1.-foo', '10 - bar', '2_quux') sort
    by that number. Titles without one fall back to alphabetic order, after
    all the numbered titles. Within each group, ties are broken by lower-cased
    title for stability.

    The leading-number sentinel for unnumbered titles is a large number
    (10**9) so they always sort after numbered titles in ascending order.
    Sentinel is an int (not None) so the tuple is fully sortable.
    """
    m = _LEADING_NUMBER_RE.match(title or "")
    if m:
        return (int(m.group(1)), (title or "").lower())
    # No leading number — push to the end with a large sentinel number
    return (10**9, (title or "").lower())


def natural_sort_key_str(title: str) -> str:
    """Render a video's natural_sort_key as a lexicographically-sortable
    string. Used as data-sort-key in templates so the JS can do a plain
    localeCompare on it.

    Format: "{number:09d}:{title_lower}"
    - The number is zero-padded to 9 digits so "1.-foo" sorts before "10.-bar"
      (lexicographic compare on "000000001:..." < "000000010:...").
    - Videos with no leading number get the same 10**9 sentinel as
      natural_sort_key so they always sort AFTER numbered videos in
      ascending order, BEFORE them in descending.

    The 9-digit padding handles up to ~1 billion numbered videos, which
    is vastly more than any real course has. Increase if needed.
    """
    n, t = natural_sort_key(title)
    return f"{n:09d}:{t}"


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
    # Cascade is needed so that deleting a Video row also deletes its
    # associated Asset rows (transcript, summary, mindmap, etc.). The
    # `ondelete="CASCADE"` on the Asset FK is a DB-level backup if
    # SQLAlchemy is bypassed (e.g. raw SQL). Used by:
    #   - app/routers/videos.py:delete_video
    #   - app/routers/courses.py:delete course + sections (indirectly)
    assets: Mapped[list["Asset"]] = relationship(
        "Asset",
        back_populates="video",
        cascade="all, delete-orphan",
    )
    # Chat sessions for this video (both flashcard-scope and
    # video-scope). Same cascade reasoning as assets above.
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession",
        back_populates="video",
        cascade="all, delete-orphan",
    )