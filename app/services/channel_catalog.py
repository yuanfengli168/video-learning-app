"""Channel catalog service — channel/playlist/video queries (2026-09-08).

Three-level browse hierarchy for the catalog:

    /catalog                → channel grid
    /channel/{slug}         → playlist grid (Courses under the channel)
    /channel/{slug}/playlist/{course_id} → video grid

All queries are visibility-aware (reuse the same rules as
app/services/catalog.py — a FREE user never sees a PAID_ONLY video,
even inside a playlist card's video count).

Playlist card covers: the FIRST video's thumbnail (YouTube playlist
style) + the visible video count. The count must respect visibility —
otherwise "12 videos" on a playlist card is a PAID_ONLY oracle for
FREE users (the same class of leak the telemetry pipeline guards
against; see app/routers/telemetry.py's tier-visibility checks).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.roles import max_visibility_for_role
from app.models import Channel, Course, Section, Video

if TYPE_CHECKING:
    from app.auth.roles import UserRole


def _visible_video_subq(max_v: int):
    """Subquery: visible videos (youtube-only, visibility-filtered).

    Used as a counting subquery so playlist cards show a
    visibility-respecting video count in ONE round-trip per page.
    """
    return (
        select(Video.id)
        .where(
            Video.youtube_id.is_not(None),
            Video.visibility <= max_v,
        )
        .subquery()
    )


def list_channels(
    db: Session,
    role: "UserRole | int | None",
) -> list[dict[str, Any]]:
    """All channels with visible-playlist + visible-video counts.

    Returns one dict per channel:
      {channel, playlist_count, video_count}

    A channel with zero VISIBLE playlists doesn't render at all —
    an empty channel tile is noise (and an ADMIN_ONLY-content oracle
    for FREE users if we showed it with a "0" count).

    One query per page load (JOIN + GROUP BY), not N+1.
    """
    max_v = max_visibility_for_role(role).value
    visible_videos = _visible_video_subq(max_v)

    rows = db.execute(
        select(
            Channel,
            func.count(func.distinct(Course.id)).label("playlist_count"),
            func.count(func.distinct(Video.id)).label("video_count"),
        )
        .join(Course, Course.channel_id == Channel.id)
        .join(Section, Section.course_id == Course.id)
        .join(Video, Video.section_id == Section.id)
        .where(Video.id.in_(select(visible_videos)))
        .group_by(Channel.id)
        .order_by(Channel.created_at.asc())
    ).all()

    return [
        {"channel": ch, "playlist_count": pc, "video_count": vc}
        for ch, pc, vc in rows
    ]


def get_channel_by_slug(db: Session, slug: str) -> Channel | None:
    return db.execute(
        select(Channel).where(Channel.slug == slug)
    ).scalar_one_or_none()


def list_playlists_in_channel(
    db: Session,
    channel: Channel,
    role: "UserRole | int | None",
) -> list[dict[str, Any]]:
    """Playlists (Courses) under a channel with YouTube-style covers.

    Returns one dict per playlist:
      {course, cover_url, cover_duration, video_count, first_video}

    Cover = the first VISIBLE video's thumbnail (playlist style).
    Playlists with zero visible videos are omitted entirely.
    """
    max_v = max_visibility_for_role(role).value

    # All visible videos in this channel, with their section/course
    # ids, ordered per-section so "first video" is the natural order.
    rows = db.execute(
        select(Video, Section.course_id, Course.title, Course.id.label("cid"))
        .join(Section, Video.section_id == Section.id)
        .join(Course, Section.course_id == Course.id)
        .where(
            Course.channel_id == channel.id,
            Video.youtube_id.is_not(None),
            Video.visibility <= max_v,
        )
        .order_by(Course.created_at.asc(), Section.order_index.asc(),
                  Video.order_index.asc())
    ).all()

    # Group by course in Python (course order preserved by the query).
    by_course: dict[str, dict[str, Any]] = {}
    for video, course_id, course_title, _cid in rows:
        entry = by_course.get(course_id)
        if entry is None:
            entry = {
                "course_id": course_id,
                "title": course_title,
                "videos": [],
            }
            by_course[course_id] = entry
        entry["videos"].append(video)

    result = []
    for entry in by_course.values():
        first = entry["videos"][0]
        result.append(
            {
                "course_id": entry["course_id"],
                "title": entry["title"],
                "cover_url": first.thumbnail_url,
                "cover_duration": first.duration,
                "cover_video_id": first.id,
                "video_count": len(entry["videos"]),
                "videos": entry["videos"],
            }
        )
    return result


def get_playlist_videos(
    db: Session,
    channel: Channel,
    course_id: str,
    role: "UserRole | int | None",
) -> list[Video] | None:
    """Visible videos for one playlist, or None if the playlist isn't
    in this channel (or has zero visible videos — treat as 404 so the
    URL space doesn't leak playlist existence)."""
    max_v = max_visibility_for_role(role).value
    course = db.execute(
        select(Course).where(
            Course.id == course_id, Course.channel_id == channel.id
        )
    ).scalar_one_or_none()
    if course is None:
        return None
    return list(
        db.execute(
            select(Video)
            .join(Section, Video.section_id == Section.id)
            .where(
                Section.course_id == course.id,
                Video.youtube_id.is_not(None),
                Video.visibility <= max_v,
            )
            .order_by(Section.order_index.asc(), Video.order_index.asc())
        )
        .scalars()
        .all()
    )