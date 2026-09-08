"""Dashboard tab queries (2026-09-09) — Top Viewed / Our Loves / Newest.

Design decisions (settled with the user 2026-09-09):

  * Entity-per-metric: Top Viewed + Our Loves are VIDEO leaderboards
    (YouTube views and our play events are per-video). Newest is a
    PLAYLIST leaderboard — a 9-video bulk import is ONE new thing, so
    one playlist card reads cleaner than nine identical cards.
  * SORT LIVE at render time; only the view-count DATA is snapshotted
    nightly (00:10 SGT launchd). No frozen daily rank = new imports
    slot into place immediately, no rank-recalc edge cases.
  * Caps (leaderboards don't paginate): Top Viewed 20, Our Loves 20,
    Newest 10 playlists + a 'See all in Catalog →' escape hatch on
    each tab.
  * Newest ranks playlists by max(video.created_at) — most recently
    ADDED video — so adding a video to an old playlist bumps it back
    to the top (YouTube 'recently updated' semantics), and a playlist
    created last week with a fresh import isn't invisible.
  * Visibility: every query is role-filtered (FREE sees PUBLIC only)
    so play/view metrics never leak PAID_ONLY content existence.
  * 'Our Loves' = rolling 30-day play events (trending semantics, not
    all-time fossils). Anonymous users see it too — aggregate counts,
    no per-user data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.roles import max_visibility_for_role
from app.models import Course, Section, Video

if TYPE_CHECKING:
    from app.auth.roles import UserRole

TOP_VIEWED_CAP = 20
OUR_LOVES_CAP = 20
NEWEST_PLAYLISTS_CAP = 10
OUR_LOVES_WINDOW_DAYS = 30


def _visible_catalog_videos(db: Session, max_v: int) -> "Any":
    """Base query: admin-curated (youtube_id set), visibility-filtered."""
    return (
        select(Video)
        .where(
            Video.youtube_id.is_not(None),
            Video.visibility <= max_v,
        )
    )


def top_viewed_videos(
    db: Session, role: "UserRole | int | None", limit: int = TOP_VIEWED_CAP
) -> list[Video]:
    """Videos by YouTube view_count desc; NULL counts sort last.

    Tie-breaker: newest first (fresh imports with no views yet sit
    above other NULL-count videos)."""
    max_v = max_visibility_for_role(role).value
    q = (
        _visible_catalog_videos(db, max_v)
        .order_by(
            Video.view_count.desc().nulls_last(),
            Video.created_at.desc(),
        )
        .limit(limit)
    )
    return list(db.execute(q).scalars().all())


def our_loves_videos(
    db: Session, role: "UserRole | int | None", limit: int = OUR_LOVES_CAP
) -> list[tuple[Video, int]]:
    """Videos by OUR play events (rolling 30-day window) desc.

    Returns (video, play_count) pairs — the count feeds the small
    'played N× this month' line and the 🔥 badge on the top 3.

    Tie-breaker: YouTube view_count desc (a play-count tie falls back
    to globally-popular, which reads sensibly)."""
    from app.models import Event

    max_v = max_visibility_for_role(role).value
    since = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=OUR_LOVES_WINDOW_DAYS)
    )
    q = (
        select(Video, func.count(Event.id).label("plays"))
        .join(Event, Event.video_id == Video.id)
        .where(
            Video.youtube_id.is_not(None),
            Video.visibility <= max_v,
            Event.source == "ui.player",
            Event.message.like("ui player play%"),
            Event.ts >= since,
        )
        .group_by(Video.id)
        .order_by(
            func.count(Event.id).desc(),
            Video.view_count.desc().nulls_last(),
            Video.created_at.desc(),
        )
        .limit(limit)
    )
    rows = db.execute(q).all()
    return [(video, int(plays)) for video, plays in rows]


def newest_playlists(
    db: Session, role: "UserRole | int | None", limit: int = NEWEST_PLAYLISTS_CAP
) -> list[dict[str, Any]]:
    """Channel playlists by most-recently-added VIDEO (created_at).

    Rank = max(video.created_at) over the playlist's VISIBLE videos —
    not playlist creation date — so importing into an old playlist
    bumps it to the top. Card cover = that newest video's thumbnail
    (the 'new thing' the user is being shown)."""
    max_v = max_visibility_for_role(role).value

    # newest visible video per channel playlist
    newest_video_sq = (
        select(
            Section.course_id.label("course_id"),
            func.max(Video.created_at).label("max_created"),
        )
        .join(Section, Video.section_id == Section.id)
        .where(
            Video.youtube_id.is_not(None),
            Video.visibility <= max_v,
        )
        .group_by(Section.course_id)
        .subquery()
    )

    rows = (
        db.execute(
            select(
                Course,
                newest_video_sq.c.max_created,
            )
            .join(newest_video_sq, newest_video_sq.c.course_id == Course.id)
            .where(Course.channel_id.is_not(None))
            .order_by(newest_video_sq.c.max_created.desc())
            .limit(limit)
        )
        .all()
    )

    result = []
    for course, max_created in rows:
        # Cover video = the newest VISIBLE video in that playlist.
        cover = db.execute(
            select(Video)
            .join(Section, Video.section_id == Section.id)
            .where(
                Section.course_id == course.id,
                Video.youtube_id.is_not(None),
                Video.visibility <= max_v,
            )
            .order_by(Video.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        channel = course.channel
        result.append(
            {
                "course": course,
                "channel": channel,
                "channel_slug": channel.slug if channel else None,
                "cover": cover,
                "newest_at": max_created,
            }
        )
    return result


def count_new_videos_since(
    db: Session, course_id: str, days: int = 7
) -> int:
    """'N new this week' helper for the Newest playlist cards."""
    since = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    )
    return int(
        db.execute(
            select(func.count(Video.id))
            .join(Section, Video.section_id == Section.id)
            .where(
                Section.course_id == course_id,
                Video.youtube_id.is_not(None),
                Video.created_at >= since,
            )
        ).scalar()
        or 0
    )


def format_views(count: int | None) -> str:
    """1813388220 → '1.8B views'; 1234567 → '1.2M views'; 1234 →
    '1.2K views'; None → ''."""
    if count is None:
        return ""
    if count >= 1_000_000_000:
        n = count / 1_000_000_000
        s = f"{n:.1f}".rstrip("0").rstrip(".")
        return f"{s}B views"
    if count >= 1_000_000:
        n = count / 1_000_000
        s = f"{n:.1f}".rstrip("0").rstrip(".")
        return f"{s}M views"
    if count >= 1_000:
        n = count / 1_000
        s = f"{n:.1f}".rstrip("0").rstrip(".")
        return f"{s}K views"
    return f"{count} views"