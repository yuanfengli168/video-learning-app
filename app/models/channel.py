"""Channel model — the top level of the catalog hierarchy (2026-09-08).

Product decision (user, 2026-09-08): the catalog becomes
YouTube-shaped:

    Channel  (OpenAI, Anthropic, ByteByteGo…)  ← NEW, this model
      └─ Course  (a "playlist")
           └─ Section  (kept for internal ordering; catalog UIs
                        treat Course as the playlist unit)
                └─ Video  (YouTube-style cards)

Channels are ADMIN-CURATED ONLY:
  - No user ever owns a channel; `user_id` is nullable and records
    only "which admin created this" (provenance metadata, not an
    ownership check — see the access rules below).
  - Only admins with CURATE_CATALOG can create/rename channels
    (via the upload page's "＋ New channel" or a future
    /admin/channels CRUD page).

Access rules (settled 2026-09-08):
  - A Course with channel_id IS NOT NULL is channel-owned catalog
    content: the channel (not any individual user) is its home.
    It appears under /channel/{slug} in the catalog.
  - A Course with channel_id IS NULL is a personal course — today's
    pre-channel behavior (ownership checks on upload/regenerate
    still key off course.user_id).
  - Videos inside channel playlists keep their own `visibility`
    (PUBLIC/PAID_ONLY/ADMIN_ONLY). Channel/playlist-level
    visibility inheritance was explicitly deferred ("not now,
    but definitely later") — the columns are NOT added here so
    nobody half-uses them.

Rendering: channels are NAME-FIRST tiles (product decision: no
brand logos — see Channel.icon_url, which is reserved but unused).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _make_slug() -> str:
    """Default slug generator — a short random suffix keeps slugs
    unique even when two channels share a display name (e.g. two
    "OpenAI" channels would clash). Real slugs are normally derived
    from the name by the router; this is the fallback so the
    NOT NULL + UNIQUE constraint can never fail an insert."""
    return uuid.uuid4().hex[:12]


class Channel(Base):
    """A catalog channel (e.g. 'OpenAI', 'Anthropic', 'ByteByteGo').

    Channels group admin-curated playlists (Courses) for browsing.
    Think YouTube channel → playlists → videos.
    """

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Display name — what renders on the tile ("OpenAI").
    # NOT the URL; the slug is separate so renames never break links.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # URL-safe unique slug (/channel/openai). Lowercase, hyphens.
    slug: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True,
        default=_make_slug,
    )
    # Optional one-line description shown on the channel page.
    description: Mapped[str] = mapped_column(String(2000), default="")
    # RESERVED for future logo/avatar uploads. Unused in v1 — tiles
    # are name-first (product decision 2026-09-08: no brand icons;
    # avoids trademark questions entirely and costs nothing).
    icon_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Provenance: which admin created the channel. NOT an access
    # control — any admin with CURATE_CATALOG can curate any channel.
    user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Playlists (Courses) that live under this channel. Only courses
    # with channel_id == this channel. Personal courses are NOT here.
    playlists: Mapped[list["Course"]] = relationship(
        "Course",
        back_populates="channel",
        # newest playlists first on the channel page
        order_by="Course.created_at.desc()",
    )