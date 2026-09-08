"""Episode position tests (Option A, 2026-09-08).

Channel playlists are sequential courses — the UI must COMMUNICATE the
order that bulk import already stores (order_index):

  * playlist page: numbered badge + "Episode N of M" per card,
    positions computed at render time from the ordered query
  * watch page: "Episode N of M" pill + prev/next links for channel
    content; NOTHING for personal uploads

Positions are NEVER stored — they're derived on every render, so
re-imports and admin re-orders stay correct automatically.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Channel, Course, Section, Video


def _mk_channel(db: Session, name: str, slug: str) -> Channel:
    ch = Channel(name=name, slug=slug, user_id="uid-admin")
    db.add(ch)
    db.flush()
    return ch


def _mk_playlist(db: Session, channel: Channel, title: str) -> tuple[Course, Section]:
    course = Course(title=title, user_id="uid-admin", channel_id=channel.id)
    db.add(course)
    db.flush()
    section = Section(title="Episodes", course_id=course.id, order_index=0)
    db.add(section)
    db.flush()
    return course, section


def _mk_video(db: Session, section: Section, yt_id: str, order: int,
              title: str | None = None) -> Video:
    v = Video(
        title=title or f"Video {order + 1}",
        youtube_id=yt_id,
        visibility=0,
        status="ready",
        filename=f"youtube:{yt_id}",
        file_path=f"https://www.youtube.com/watch?v={yt_id}",
        file_size=0,
        section_id=section.id,
        order_index=order,
    )
    db.add(v)
    db.flush()
    return v


YT_IDS = [  # exactly 11 chars each (lesson from test_bulk_import)
    "episod00001", "episod00002", "episod00003",
]


def test_playlist_page_shows_episode_badges(client: TestClient, db_session: Session):
    ch = _mk_channel(db_session, "Claude", "claude")
    course, sec = _mk_playlist(db_session, ch, "Claude Code 101")
    for i, yt in enumerate(YT_IDS):
        _mk_video(db_session, sec, yt, order=i)
    db_session.commit()

    resp = client.get(f"/channel/claude/playlist/{course.id}")
    assert resp.status_code == 200
    html = resp.text
    # Numbered badges 1..3 render (render-time loop.index). Jinja puts
    # whitespace around {{ loop.index }}, so match the number loosely:
    # the badge span has bg-indigo-600 and the bare number inside it.
    import re
    for n in (1, 2, 3):
        assert re.search(
            rf'rounded-full[^>]*>\s*{n}\s*</span>', html
        ), f"episode badge {n} missing"
    # "Episode N of 3" per card
    for n in (1, 2, 3):
        assert f"Episode {n} of 3" in html


def test_watch_page_episode_bar(client: TestClient, db_session: Session):
    ch = _mk_channel(db_session, "Claude", "claude")
    course, sec = _mk_playlist(db_session, ch, "Claude Code 101")
    vids = [_mk_video(db_session, sec, yt, order=i)
            for i, yt in enumerate(YT_IDS)]
    db_session.commit()

    # Middle video: has BOTH prev and next
    resp = client.get(f"/video/{vids[1].id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Episode 2 of 3" in html
    assert f"Ep 1: {vids[0].title}" in html
    assert f"/video/{vids[0].id}" in html
    assert f"Ep 3: {vids[2].title}" in html
    assert f"/video/{vids[2].id}" in html

    # First video: prev hidden, next shown
    html1 = client.get(f"/video/{vids[0].id}").text
    assert "Episode 1 of 3" in html1
    assert "Ep 2:" in html1
    assert "Ep 3:" not in html1

    # Last video: next hidden, prev shown
    html3 = client.get(f"/video/{vids[2].id}").text
    assert "Episode 3 of 3" in html3
    assert "Ep 2:" in html3
    assert "Ep 4" not in html3


def test_watch_page_personal_video_has_no_episode_ui(
    client: TestClient, db_session: Session
):
    """Personal uploads (no channel) render no episode bar — the
    feature is playlist-course-specific by design."""
    course = Course(title="My Course", user_id="uid-admin")  # channel_id None
    db_session.add(course)
    db_session.flush()
    sec = Section(title="Week 1", course_id=course.id, order_index=0)
    db_session.add(sec)
    db_session.flush()
    v = _mk_video(db_session, sec, "persnl00001", order=0)
    db_session.commit()

    html = client.get(f"/video/{v.id}").text
    assert "Episode" not in html
    assert "episode_info" not in html


def test_episode_positions_survive_reorder(
    client: TestClient, db_session: Session
):
    """Render-time computation (not stored numbers) means re-ordering
    the videos immediately changes the displayed positions."""
    ch = _mk_channel(db_session, "Claude", "claude")
    course, sec = _mk_playlist(db_session, ch, "Claude Code 101")
    vids = [_mk_video(db_session, sec, yt, order=i)
            for i, yt in enumerate(YT_IDS)]
    db_session.commit()

    # Swap orders: what was last becomes first
    vids[0].order_index, vids[2].order_index = 2, 0
    db_session.commit()

    html = client.get(f"/channel/claude/playlist/{course.id}").text
    # The formerly-last video now shows badge 1
    assert "Episode 1 of 3" in html
    # and the formerly-first shows badge 3
    assert "Episode 3 of 3" in html