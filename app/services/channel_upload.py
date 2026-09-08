"""Channel resolution for the admin upload flow (2026-09-08).

Decides, given the admin's upload-form picks, which Channel +
Course(=playlist) + Section a new video lands in.

The form offers (all optional):
  * channel_id            — pick an existing channel
  * new_channel_name      — create a new channel inline
  * new_playlist_title    — create a new playlist (Course) inline
  * section_id            — the pre-existing personal-course picker

Resolution precedence (settled 2026-09-08):
  1. new_channel_name → create (or reuse by exact name) that channel
  2. else channel_id  → that existing channel
  3. else             → NO channel: the old personal-course flow
     (section_id → admin's own course → auto-create default)

When a channel IS resolved:
  * new_playlist_title → create a new Course under the channel with
    one Section ("Episodes", order_index=0); the video lands there.
  * else → the channel's most recent playlist's first section
    (uploads stack into the playlist you're currently filling);
    if the channel has no playlists yet, create "Main Playlist".

The video's visibility is NOT inferred from the channel — per-Video
visibility remains the only rule (product decision 2026-09-08).
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Channel, Course, Section

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Display name → URL slug: lowercase, non-alnum → hyphens, trimmed.

    'ByteByteGo!' → 'bytebytego'; '  Open AI ' → 'open-ai'.
    Falls back to 'channel' if the name has no usable characters
    (uniqueness is then guaranteed by the caller's suffix)."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "channel"


def _unique_slug(db: Session, base: str) -> str:
    """Return a slug not yet taken: `base`, `base-2`, `base-3`, …"""
    slug = base
    n = 2
    while db.execute(
        select(Channel.id).where(Channel.slug == slug)
    ).scalar_one_or_none():
        slug = f"{base}-{n}"
        n += 1
    return slug


def get_or_create_channel_by_name(
    db: Session, name: str, creator_uid: str
) -> Channel:
    """Find a channel by exact display name, or create it.

    Reuse-by-name keeps the inline '＋ New channel' form from
    duplicating channels when the admin forgets one exists (the
    friendly-path assumption: an exact-name match is the same
    channel the admin means). Slug collisions get a numeric suffix.
    """
    existing = db.execute(
        select(Channel).where(Channel.name == name.strip())
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    ch = Channel(
        name=name.strip(),
        slug=_unique_slug(db, slugify(name)),
        user_id=creator_uid or None,
    )
    db.add(ch)
    db.flush()  # get the id before the Course FK references it
    return ch


def create_playlist_in_channel(
    db: Session, channel: Channel, title: str
) -> Course:
    """New Course (playlist) under a channel, with one 'Episodes' section."""
    course = Course(
        title=title.strip(),
        user_id=channel.user_id or "",  # provenance; nullable-in-spirit
        channel_id=channel.id,
    )
    db.add(course)
    db.flush()
    section = Section(title="Episodes", course_id=course.id, order_index=0)
    db.add(section)
    db.flush()
    return course


def latest_playlist_section(db: Session, channel: Channel) -> Section:
    """The first Section of the channel's newest playlist — where an
    upload lands when the admin picked a channel but no new playlist.

    Creates a 'Main Playlist' if the channel has no playlists yet."""
    course = db.execute(
        select(Course)
        .where(Course.channel_id == channel.id)
        .order_by(Course.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if course is None:
        course = create_playlist_in_channel(db, channel, "Main Playlist")
        return course.sections[0]
    section = db.execute(
        select(Section)
        .where(Section.course_id == course.id)
        .order_by(Section.order_index.asc())
        .limit(1)
    ).scalar_one_or_none()
    if section is None:
        section = Section(title="Episodes", course_id=course.id, order_index=0)
        db.add(section)
        db.flush()
    return section


def resolve_channel_target(
    db: Session,
    uid: str,
    *,
    channel_id: Optional[str],
    new_channel_name: Optional[str],
    new_playlist_title: Optional[str],
    existing_playlist_id: Optional[str] = None,
) -> tuple[Optional[Channel], Optional[Course], Optional[Section]]:
    """Resolve (channel, new_playlist, section) for an upload request.

    Returns:
      (channel, created_playlist, section_to_use)
      - channel None → caller falls back to the personal-course flow
        (resolve_section_for_new_video) exactly as before.
      - created_playlist is set only when WE created a new Course
        (so the response can tell the admin it happened).

    Raises ValueError on bad input (unknown channel_id etc.).
    """
    # 1. Which channel?
    if new_channel_name and new_channel_name.strip():
        channel = get_or_create_channel_by_name(
            db, new_channel_name, uid
        )
    elif channel_id:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id!r} not found")
    else:
        return None, None, None  # personal-course flow

    # 2. Which playlist / section?
    if new_playlist_title and new_playlist_title.strip():
        course = create_playlist_in_channel(db, channel, new_playlist_title)
        section = course.sections[0]
        return channel, course, section

    # 2026-09-08: explicit existing-playlist pick (user report —
    # channels created earlier had no way to be targeted directly).
    # Must belong to THIS channel (a course id from another channel
    # or a personal course → ValueError, not silent cross-wiring).
    if existing_playlist_id:
        course = db.execute(
            select(Course).where(
                Course.id == existing_playlist_id,
                Course.channel_id == channel.id,
            )
        ).scalar_one_or_none()
        if course is None:
            raise ValueError(
                f"Playlist {existing_playlist_id!r} not found in "
                f"channel {channel.name!r}"
            )
        section = db.execute(
            select(Section)
            .where(Section.course_id == course.id)
            .order_by(Section.order_index.asc())
            .limit(1)
        ).scalar_one_or_none()
        if section is None:
            section = Section(
                title="Episodes", course_id=course.id, order_index=0
            )
            db.add(section)
            db.flush()
        return channel, None, section

    section = latest_playlist_section(db, channel)
    return channel, None, section