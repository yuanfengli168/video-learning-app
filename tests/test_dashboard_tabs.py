"""Dashboard tabs tests (2026-09-09) — Top Viewed / Our Loves / Newest.

Decisions under test (settled with the user):
  * Entity-per-metric: Top Viewed + Our Loves = videos; Newest = playlists
  * Caps: 20 / 20 / 10 (leaderboards don't paginate)
  * Top Viewed default tab; live sort at render
  * Newest ranks playlists by most-recently-added VIDEO (bump semantics)
  * Our Loves = 30-day rolling plays; 🔥 top 3; count shown only >= 5
  * Visibility: FREE never sees PAID_ONLY content in any tab
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone

from app.models import Channel, Course, Event, Section, Video

TOP_VIEWED_CAP = 20
OUR_LOVES_CAP = 20
NEWEST_CAP = 10


def _mk(db: Session, *, views: list[int | None]):
    """Channel + playlist + one video per view count, in order."""
    ch = Channel(name="Ch", slug="ch", user_id="u")
    db.add(ch)
    db.flush()
    course = Course(title="P", user_id="u", channel_id=ch.id)
    db.add(course)
    db.flush()
    sec = Section(title="E", course_id=course.id, order_index=0)
    db.add(sec)
    db.flush()
    vids = []
    for i, vc in enumerate(views):
        yt = f"view{str(i).zfill(3)}v{i}"
        v = Video(
            title=f"V{i}", youtube_id=yt[:11], visibility=0,
            status="ready", filename="x", file_path="x", file_size=0,
            section_id=sec.id, order_index=i, view_count=vc,
        )
        db.add(v)
        db.flush()
        vids.append(v)
    db.commit()
    return ch, course, vids


def test_top_viewed_sorts_by_views_desc_nulls_last(db_session: Session):
    from app.services.dashboard_tabs import top_viewed_videos

    ch, course, vids = _mk(db_session, views=[500, 1_200_000, None, 30])
    result = top_viewed_videos(db_session, role=None)
    assert [v.title for v in result] == ["V1", "V0", "V3", "V2"]


def test_top_viewed_cap_20(db_session: Session):
    from app.services.dashboard_tabs import top_viewed_videos

    ch, course, vids = _mk(db_session, views=list(range(25)))
    result = top_viewed_videos(db_session, role=None)
    assert len(result) == TOP_VIEWED_CAP


def test_top_viewed_respects_visibility(db_session: Session):
    from app.services.dashboard_tabs import top_viewed_videos
    from app.auth.roles import UserRole

    ch, course, vids = _mk(db_session, views=[100, 900])
    vids[1].visibility = 1  # PAID_ONLY
    db_session.commit()

    # FREE (and anonymous) → only the PUBLIC one
    free_result = top_viewed_videos(db_session, role=UserRole.FREE)
    assert [v.title for v in free_result] == ["V0"]
    # ADMIN → both
    admin_result = top_viewed_videos(db_session, role=UserRole.ADMIN)
    assert len(admin_result) == 2


def _mk_plays(db: Session, vid: Video, n: int, *, days_ago: int = 0):
    ts = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(days=days_ago)
    )
    for _ in range(n):
        db.add(Event(
            ts=ts, level="INFO", source="ui.player",
            message="ui player play", user_id="u", video_id=vid.id,
            context_json="{}",
        ))
    db.flush()


def test_our_loves_30day_window(db_session: Session):
    from app.services.dashboard_tabs import our_loves_videos

    ch, course, vids = _mk(db_session, views=[10, 5])
    _mk_plays(db_session, vids[0], 3)          # recent
    _mk_plays(db_session, vids[1], 10, days_ago=45)  # OUTSIDE window
    db_session.commit()

    result = our_loves_videos(db_session, role=None)
    titles = [v.title for v, _ in result]
    assert titles == ["V0"], "45-day-old plays must not count"
    assert result[0][1] == 3


def test_our_loves_tiebreak_by_youtube_views(db_session: Session):
    from app.services.dashboard_tabs import our_loves_videos

    ch, course, vids = _mk(db_session, views=[1_000, 50])
    _mk_plays(db_session, vids[0], 2)
    _mk_plays(db_session, vids[1], 2)
    db_session.commit()

    result = our_loves_videos(db_session, role=None)
    assert result[0][0].title == "V0", "play tie → higher YT views wins"


def test_newest_playlists_ranked_by_most_recent_video(db_session: Session):
    from app.services.dashboard_tabs import newest_playlists

    ch = Channel(name="Ch", slug="ch", user_id="u")
    db_session.add(ch)
    db_session.flush()
    # OLD playlist created first, but receives a NEW video later
    old_course = Course(title="Old PL", user_id="u", channel_id=ch.id,
                        created_at=datetime(2026, 9, 1, 12, 0, 0))
    db_session.add(old_course)
    db_session.flush()
    old_sec = Section(title="E", course_id=old_course.id, order_index=0)
    db_session.add(old_sec)
    db_session.flush()
    v_old = Video(
        title="Freshly added", youtube_id="freshadd01", visibility=0,
        status="ready", filename="x", file_path="x", file_size=0,
        section_id=old_sec.id, order_index=0,
        created_at=datetime(2026, 9, 9, 12, 0, 0),
    )
    db_session.add(v_old)
    # NEWER playlist, but its newest video is OLDER than v_old
    new_course = Course(title="New PL", user_id="u", channel_id=ch.id,
                        created_at=datetime(2026, 9, 8, 12, 0, 0))
    db_session.add(new_course)
    db_session.flush()
    new_sec = Section(title="E", course_id=new_course.id, order_index=0)
    db_session.add(new_sec)
    db_session.flush()
    db_session.add(Video(
        title="Older vid", youtube_id="oldervid01", visibility=0,
        status="ready", filename="x", file_path="x", file_size=0,
        section_id=new_sec.id, order_index=0,
        created_at=datetime(2026, 9, 7, 12, 0, 0),
    ))
    db_session.commit()

    result = newest_playlists(db_session, role=None)
    assert [e["course"].title for e in result] == ["Old PL", "New PL"], (
        "Old playlist bumps to #1 because its newest video is the "
        "most recently added video overall"
    )
    assert result[0]["cover"].title == "Freshly added"


def test_newest_excludes_personal_courses(db_session: Session):
    from app.services.dashboard_tabs import newest_playlists

    personal = Course(title="Personal", user_id="u")  # channel_id None
    db_session.add(personal)
    db_session.flush()
    sec = Section(title="E", course_id=personal.id, order_index=0)
    db_session.add(sec)
    db_session.flush()
    db_session.add(Video(
        title="P", youtube_id="personalv01", visibility=0,
        status="ready", filename="x", file_path="x", file_size=0,
        section_id=sec.id, order_index=0,
    ))
    db_session.commit()

    result = newest_playlists(db_session, role=None)
    assert all(e["course"].channel_id is not None for e in result)


def test_dashboard_renders_three_tabs(client: TestClient, db_session: Session):
    ch, course, vids = _mk(db_session, views=[1_000])
    _mk_plays(db_session, vids[0], 6)
    db_session.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="dashtab-top-viewed"' in html
    assert 'id="dashtab-our-loves"' in html
    assert 'id="dashtab-newest"' in html
    assert 'id="dashpanel-top-viewed"' in html
    assert 'id="dashpanel-our-loves"' in html
    assert 'id="dashpanel-newest"' in html
    # Top Viewed is the default (panel not hidden; others hidden)
    assert 'id="dashpanel-top-viewed"' in html
    assert 'id="dashpanel-our-loves" class="hidden"' in html
    # Metric text: 1,000 views formats as "1K views"
    assert "1K views" in html
    # Our Loves: 6 plays → "played 6× this month" + 🔥 badge
    assert "played 6× this month" in html
    # Newest: playlist card links to the playlist page
    assert f"/channel/ch/playlist/{course.id}" in html
    # Tab-toggle JS exists
    assert "function dashTab" in html


def test_our_loves_hides_small_counts(client: TestClient, db_session: Session):
    """Play counts < 5 are suppressed (small numbers undermine the
    shop-window feel)."""
    ch, course, vids = _mk(db_session, views=[10])
    _mk_plays(db_session, vids[0], 2)  # below threshold
    db_session.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "played 2× this month" not in resp.text


def test_format_views():
    from app.services.dashboard_tabs import format_views

    assert format_views(None) == ""
    assert format_views(0) == "0 views"
    assert format_views(999) == "999 views"
    assert format_views(1_000) == "1K views"
    assert format_views(1_234) == "1.2K views"
    assert format_views(1_000_000) == "1M views"
    assert format_views(1_234_567) == "1.2M views"
    assert format_views(2_400_000) == "2.4M views"
    # Billions (caught live 2026-09-09: Rick Astley 1.81B was rendering
    # as '1813.4M views' before the B branch existed)
    assert format_views(1_000_000_000) == "1B views"
    assert format_views(1_813_388_220) == "1.8B views"