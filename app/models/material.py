"""MVP0.2: course materials (PDF / .md / .txt / .zip uploads).

`PocketMaterial`      — one uploaded file. Linked to a section (visible to
                       all videos in the section) and/or to a single
                       video (per-video note). The Mac web app is the
                       only place that uploads; the iOS app is a
                       read-only mirror.

`PocketVideoMaterial` — many-to-many join between videos and materials.
                       A row exists only when the user has selected
                       that material for that video's LLM context.
                       When the user uploads a file from the video
                       picker, we create this row immediately so the
                       file is auto-selected for that video.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PocketMaterial(Base):
    """An uploaded file the user wants the LLM to see as context.

    Visibility:
      - section_id set, video_id NULL → section-scope. All videos in
        that section see this material in their picker.
      - video_id set, section_id NULL → video-scope. Only this video's
        picker shows it. (The user can still share it later via the
        picker — but currently the data model allows the row to stay
        video-scoped; selection is in `PocketVideoMaterial`.)
      - both set → uploaded from the video picker of that section's
        video. Visible in the section's master list AND in the
        video's picker. Auto-selected for this video (via the
        PocketVideoMaterial row created at upload time).

    The `extracted_text` is the LLM-readable form. It's filled in
    synchronously on upload (PDFs up to ~100 pages complete in
    <60s; we time out and mark `status=failed` if longer).
    """

    __tablename__ = "pocket_materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # Ownership
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Visibility scope (one or both may be set; semantics above)
    section_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sections.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    video_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("videos.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # File metadata
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Extraction state
    # "processing" | "ready" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # MVP0.2 followup: provenance of how the text was extracted. Drives
    # the UI badge color + the "Switch to a vision-capable tutor" hint
    # when text is image-only. NULL = legacy row from before this column
    # existed (treated as "pypdf" — the prior behavior).
    #
    # Valid values: "pypdf" | "vision" | "ollama_vision" | "tesseract"
    # Stored as VARCHAR (SQLite doesn't honor CHECK in older versions).
    extraction_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PocketVideoMaterial(Base):
    """Many-to-many: which materials are in the LLM context for which video.

    Created when:
      - User selects a material in the video picker
      - User uploads a file from inside the video picker (auto-selected)

    Deleted when:
      - User deselects in the picker
      - User deletes the material (cascade)
      - User deletes the video (cascade — see MVP0.2 §3)

    No fields beyond the join + selection timestamp. We do NOT store the
    material's text here — that lives in `PocketMaterial.extracted_text`
    so we don't duplicate large blobs.
    """

    __tablename__ = "pocket_video_materials"
    __table_args__ = (
        UniqueConstraint("video_id", "material_id", name="uq_pocket_video_material"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    video_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    material_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pocket_materials.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # When the user added this material to the video's context
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
