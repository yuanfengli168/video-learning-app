"""PluginRun model — audit log for every plugin invocation (MVP2.1.0).

What this is:
  Every time a user runs a plugin (e.g. WebM -> MP4
  transcode), a row is written to the `plugin_runs` table
  with the outcome. This is the audit log — used by the
  UI to show "last transcode: 2 hours ago, 1.2 GB MP4
  written" and by the support / debug path to answer
  "what plugins did this user run and what happened".

Schema:
  id           UUID
  video_id     FK -> videos.id (cascade delete)
  plugin_key   the plugin name (e.g. "webm_to_mp4")
  ok           boolean — was the plugin successful?
  message      human-readable summary / error message
  output_path  path to the output file (NULL for plugins
               that don't write a file, e.g. future
               "extract metadata" plugin)
  extra_json   stringified dict for plugin-specific data
               (e.g. {"size_bytes": 1234567})
  created_at   timestamp

Why stringified JSON for `extra`:
  We could use SQLAlchemy's JSON type, but SQLite stores
  JSON as TEXT under the hood, and stringifying keeps
  the schema simple. v1 only has one plugin; if more
  plugins land with structured extras, we can migrate
  to JSONB / native JSON.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PluginRun(Base):
    """A single plugin invocation. One row per run, per video.

    Cascade-deleted when the parent video is deleted, so
    the audit log doesn't outlive the video. This means
    hard-delete (the current default) removes the audit
    log too — which is fine for v1 because we don't have
    soft-delete yet (that's MVP3.0 #5).
    """

    __tablename__ = "plugin_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    video_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # the UI fetches by video, so index this
    )
    plugin_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    output_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # back-reference to the video. Not used by the app yet
    # (we always query from the video side), but defining it
    # makes the relationship discoverable in the ORM and
    # enables cascade-delete through SQLAlchemy (the
    # ForeignKey(ondelete="CASCADE") above handles the
    # database side; this relationship is for Python access).
    video: Mapped["Video"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Video", back_populates="plugin_runs"
    )
