"""Event model — structured audit log for everything interesting that happens.

What this is:
  Every noteworthy action (caption job started, LLM call succeeded/failed,
  rate limit hit, admin action, etc.) writes one row to the `events` table.
  The /admin/events page reads from this table to show the operator what
  is happening in real time.

Schema (kept narrow on purpose — see context_json for free-form data):
  id           UUID string
  ts           DATETIME (server-side default = now)
  level        INFO | WARNING | ERROR
  source       dotted path like 'services.youtube_captions_job'
  message      human-readable summary
  user_id      Firebase UID if the event was caused by a user action (NULL = system)
  video_id     Video.id if the event relates to a specific video (NULL = general)
  context_json stringified dict for structured metadata (latency_ms,
               provider, model, error_type, …)

Why a separate table from plugin_runs:
  - plugin_runs is for plugin executions only (transcode, etc.)
  - events is the general-purpose audit log; plugin_runs could later be
    replaced by emitting events instead of writing rows directly
  - Different access patterns (events is high-volume, admin-read;
    plugin_runs is low-volume, user-read)

Why SQLite JSON-as-text for context_json:
  Same rationale as plugin_run.extra_json — SQLite stores JSON as TEXT
  natively, SQLAlchemy's JSON type would force us to load the value as
  a Python dict on every read even when we only need to display it.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    """A single audit log entry."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    # INFO / WARNING / ERROR (string, not enum — easier to grep from logs)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # dotted path, e.g. "services.youtube_captions_job"
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional causal link to a Firebase UID
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Optional causal link to a Video row
    video_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Free-form JSON-encoded dict for structured details
    context_json: Mapped[str] = mapped_column(Text, default="")

    # Composite indexes for common dashboard queries
    __table_args__ = (
        Index("ix_events_level_ts", "level", "ts"),
        Index("ix_events_source_ts", "source", "ts"),
        Index("ix_events_video_ts", "video_id", "ts"),
    )
