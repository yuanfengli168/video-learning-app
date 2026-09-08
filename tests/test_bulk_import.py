"""Bulk import tests (2026-09-08).

POST /api/admin/videos/youtube/bulk:
  * urls mode — order preserved via order_index
  * playlist_url mode — expands via playlistItems.list (mocked)
  * idempotency — already-cataloged youtube_ids skipped + reported
  * in-request dedupe — same URL twice in one request adds once
  * channel required — no channel → 400 (bulk doesn't do personal flow)
  * existing_playlist_id — videos land in that playlist's section
  * by-name playlist lookup endpoint for the form
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import patch

from app.models import Channel, Course, Section, Video


@pytest.fixture(autouse=True)
def _disable_youtube_api(monkeypatch):
    """Kill enrichment for most tests (same rationale as
    test_admin_router) — but NOT the key itself: the playlist-expansion
    test constructs a real YouTubeAPIClient (key required) with only
    list_playlist_videos mocked."""
    from app.services import youtube_api
    original = youtube_api.settings.youtube_api_key
    youtube_api.settings.youtube_api_key = ""
    # Stub the staggered caption job — background tasks would otherwise
    # run real yt-dlp against our fake IDs (slow, network-dependent).
    import app.routers.admin as admin_router
    monkeypatch.setattr(
        admin_router, "_staggered_caption_job",
        lambda video_id, delay: None,
    )
    yield
    youtube_api.settings.youtube_api_key = original


def _promote_admin(db_session: Session):
    from app.auth.admin import ensure_user_row
    from sqlalchemy import text
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()


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


def _bulk_post(client: TestClient, **payload):
    return client.post(
        "/api/admin/videos/youtube/bulk",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )


def test_bulk_urls_preserves_order(admin_client: TestClient, db_session: Session):
    _promote_admin(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = _bulk_post(
            admin_client,
            urls=[
                "https://www.youtube.com/watch?v=order000001",
                "https://www.youtube.com/watch?v=order000002",
                "https://www.youtube.com/watch?v=order000003",
            ],
            new_channel_name="Ordered",
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 3
    assert body["skipped"] == 0
    assert body["created_channel"] is True

    rows = (
        db_session.query(Video)
        .order_by(Video.order_index.asc())
        .all()
    )
    yts = [v.youtube_id for v in rows]
    assert yts == ["order000001", "order000002", "order000003"]
    idxs = [v.order_index for v in rows]
    assert idxs == sorted(idxs) and len(set(idxs)) == 3


def test_bulk_skips_existing_and_in_request_dupes(
    admin_client: TestClient, db_session: Session
):
    _promote_admin(db_session)
    ch = _mk_channel(db_session, "OpenAI", "openai")
    course, sec = _mk_playlist(db_session, ch, "Talks")
    # Pre-existing video in the catalog
    existing = Video(
        title="Existing", youtube_id="existing001", visibility=0,
        status="ready", filename="youtube:existing001",
        file_path="https://x", file_size=0, section_id=sec.id, order_index=0,
    )
    db_session.add(existing)
    db_session.commit()

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = _bulk_post(
            admin_client,
            urls=[
                "https://www.youtube.com/watch?v=existing001",  # in catalog
                "https://www.youtube.com/watch?v=newvid00001",
                "https://www.youtube.com/watch?v=newvid00001",  # dupe in request
                "https://www.youtube.com/watch?v=newvid00002",
            ],
            channel_id=ch.id,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 2        # newvid00001 + newvid00002
    assert body["skipped"] == 2      # existing001 + in-request dupe
    # Per-entry statuses in order. NB a dict keyed by youtube_id would
    # collapse the two newvid00001 entries (last-wins) — assert on the
    # ordered list instead.
    entry_statuses = [(r["youtube_id"], r["status"]) for r in body["results"]]
    assert entry_statuses == [
        ("existing001", "skipped"),
        ("newvid00001", "added"),
        ("newvid00001", "skipped"),   # in-request duplicate
        ("newvid00002", "added"),
    ]

    # Re-run the same request: everything skipped (idempotent)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp2 = _bulk_post(
            admin_client,
            urls=[
                "https://www.youtube.com/watch?v=existing001",
                "https://www.youtube.com/watch?v=newvid00001",
                "https://www.youtube.com/watch?v=newvid00002",
            ],
            channel_id=ch.id,
        )
    body2 = resp2.json()
    assert body2["added"] == 0
    assert body2["skipped"] == 3


def test_bulk_requires_channel(admin_client: TestClient, db_session: Session):
    _promote_admin(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = _bulk_post(
            admin_client,
            urls=["https://www.youtube.com/watch?v=nochannel1"],
        )
    assert resp.status_code == 400
    assert "channel" in resp.json()["detail"].lower()


def test_bulk_neither_urls_nor_playlist(admin_client: TestClient, db_session: Session):
    _promote_admin(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = _bulk_post(admin_client, new_channel_name="X")
    assert resp.status_code == 400


def test_bulk_existing_playlist_id(
    admin_client: TestClient, db_session: Session
):
    """Videos land in the picked playlist (not the newest one)."""
    _promote_admin(db_session)
    ch = _mk_channel(db_session, "Claude", "claude")
    course, sec = _mk_playlist(db_session, ch, "Claude Code 101")
    _mk_playlist(db_session, ch, "Newest Playlist")  # would win if blank
    db_session.commit()

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = _bulk_post(
            admin_client,
            urls=["https://www.youtube.com/watch?v=intopl00001"],
            channel_id=ch.id,
            existing_playlist_id=course.id,
        )
    assert resp.status_code == 200, resp.text
    v = db_session.query(Video).filter_by(youtube_id="intopl00001").one()
    landed = db_session.get(Course, v.section.course_id)
    assert landed.title == "Claude Code 101"


def test_bulk_playlist_url_expansion(
    admin_client: TestClient, db_session: Session
):
    """playlist_url mode: playlistItems.list is mocked to return 2
    videos in order; they land with those titles, in order."""
    from app.services.youtube_api import VideoMetadata
    from app.services import youtube_api as _ya

    _promote_admin(db_session)
    ch = _mk_channel(db_session, "OpenAI", "openai")

    fake_videos = [
        VideoMetadata(
            youtube_id="plexpand0001", title="PL Video One",
            channel="OpenAI", thumbnail_url="https://t/1.jpg",
            duration_seconds=0, caption_tracks=[],
        ),
        VideoMetadata(
            youtube_id="plexpand0002", title="PL Video Two",
            channel="OpenAI", thumbnail_url="https://t/2.jpg",
            duration_seconds=0, caption_tracks=[],
        ),
    ]

    # This mode needs a real API key to construct the client — restore
    # it just for this test (the client's list_playlist_videos is
    # fully mocked, so no network call happens).
    original_key = _ya.settings.youtube_api_key
    _ya.settings.youtube_api_key = "test-key-for-playlist-mode"
    try:
        with patch("app.auth.dependencies.verify_token",
                   return_value={"uid": "uid-admin", "email": "admin@x.com"}), \
             patch("app.services.youtube_api.YouTubeAPIClient.list_playlist_videos",
                   return_value=fake_videos) as mock_list:
            resp = _bulk_post(
                admin_client,
                playlist_url="https://www.youtube.com/playlist?list=PLtest123",
                channel_id=ch.id,
            )
    finally:
        _ya.settings.youtube_api_key = original_key
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["added"] == 2
    assert mock_list.called

    rows = (
        db_session.query(Video)
        .filter(Video.youtube_id.in_(["plexpand0001", "plexpand0002"]))
        .order_by(Video.order_index.asc())
        .all()
    )
    assert [v.title for v in rows] == ["PL Video One", "PL Video Two"]


def test_channel_playlists_endpoint(
    admin_client: TestClient, db_session: Session
):
    """GET /api/admin/channels/{id}/playlists — newest first, with the
    by-name variant for typed channel names."""
    from datetime import datetime, timedelta

    _promote_admin(db_session)
    ch = _mk_channel(db_session, "Claude", "claude")
    c1, _ = _mk_playlist(db_session, ch, "First Playlist")
    c1.created_at = datetime(2026, 9, 1, 12, 0, 0)
    db_session.flush()
    c2, _ = _mk_playlist(db_session, ch, "Claude Code 101")
    c2.created_at = datetime(2026, 9, 2, 12, 0, 0)  # explicitly newer
    db_session.commit()

    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        r1 = admin_client.get(f"/api/admin/channels/{ch.id}/playlists")
        r2 = admin_client.get(
            "/api/admin/channels/by-name/playlists?name=Claude"
        )
        r3 = admin_client.get(
            "/api/admin/channels/by-name/playlists?name=Nope"
        )
    assert r1.status_code == 200
    data = r1.json()
    titles = [p["title"] for p in data["playlists"]]
    # newest first
    assert titles[0] == "Claude Code 101"
    assert "First Playlist" in titles

    assert r2.status_code == 200
    assert r2.json()["channel_id"] == ch.id

    assert r3.status_code == 404  # unknown name → friendly 404


def test_upload_page_renders_bulk_ui(admin_client: TestClient, db_session: Session):
    _promote_admin(db_session)
    with patch("app.auth.dependencies.verify_token",
               return_value={"uid": "uid-admin", "email": "admin@x.com"}):
        resp = admin_client.get("/admin/upload")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="bulk-urls"' in html
    assert 'id="bulk-submit-btn"' in html
    assert 'id="existing_playlist_id"' in html
    assert "youtube/bulk" in html