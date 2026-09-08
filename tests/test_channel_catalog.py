"""Channel catalog tests (2026-09-08).

Covers the YouTube-shaped browse hierarchy:
  Channel → Course (=playlist) → Section → Video

  * Model: slug uniqueness fallback, course.channel_id FK
  * Upload flow: create channel inline, create playlist inline,
    reuse channel by exact name, blank playlist → latest playlist,
    personal-course flow untouched (no channel fields)
  * Browse pages: /catalog channel grid, /channel/{slug} playlist
    grid with covers + counts, /channel/{slug}/playlist/{id} video
    grid
  * Visibility: FREE users never see PAID_ONLY content in counts
    or cards (no tier-content oracle)
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Channel, Course, Section, Video


@pytest.fixture(autouse=True)
def _disable_youtube_api():
    """Disable YouTube Data API enrichment (same as test_admin_router):
    the test env has a real YOUTUBE_API_KEY, which would 400 on our
    fake 11-char IDs (correctly!) instead of skipping enrichment."""
    from app.services import youtube_api
    original = youtube_api.settings.youtube_api_key
    youtube_api.settings.youtube_api_key = ""
    yield
    youtube_api.settings.youtube_api_key = original


# ── Helpers ──────────────────────────────────────────────────────────────


def _mk_channel(db: Session, name: str, slug: str | None = None) -> Channel:
    ch = Channel(name=name, slug=slug or uuid.uuid4().hex[:8], user_id="uid-admin")
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


def _mk_video(
    db: Session, section: Section, yt_id: str,
    *, visibility: int = 0, status: str = "ready",
) -> Video:
    v = Video(
        title=f"Video {yt_id}",
        youtube_id=yt_id,
        visibility=visibility,
        status=status,
        filename=f"youtube:{yt_id}",
        file_path=f"https://www.youtube.com/watch?v={yt_id}",
        file_size=0,
        section_id=section.id,
        thumbnail_url=f"https://img.example.com/{yt_id}.jpg",
        channel="Test Channel",
        duration=125,
    )
    db.add(v)
    db.flush()
    return v


# ── Model ────────────────────────────────────────────────────────────────


def test_channel_slug_unique_fallback(db_session: Session):
    """Two channels with the same name get distinct slugs (numeric suffix)."""
    from app.services.channel_upload import get_or_create_channel_by_name

    a = get_or_create_channel_by_name(db_session, "OpenAI", "u1")
    b = get_or_create_channel_by_name(db_session, "OpenAI", "u2")
    # Exact-name match → SAME channel reused, not duplicated
    assert a.id == b.id
    assert db_session.query(Channel).count() == 1


def test_channel_two_distinct_names(db_session: Session):
    from app.services.channel_upload import (
        get_or_create_channel_by_name, _unique_slug,
    )

    a = get_or_create_channel_by_name(db_session, "OpenAI", "u1")
    b = get_or_create_channel_by_name(db_session, "Anthropic", "u2")
    assert a.id != b.id
    assert a.slug != b.slug
    # slugify handles spaces/punctuation
    assert _unique_slug(db_session, "openai") == "openai-2"


# ── Upload flow ──────────────────────────────────────────────────────────


def _admin_headers():
    return {"Authorization": "Bearer fake"}


def _promote_admin(db_session: Session):
    from app.auth.admin import ensure_user_row
    from sqlalchemy import text
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()


def test_upload_creates_channel_and_playlist_inline(
    admin_client: TestClient, db_session: Session
):
    """new_channel_name + new_playlist_title → channel + course + section
    all created, video lands inside, response reports both creations."""
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        resp = admin_client.post(
            "/api/admin/videos/youtube",
            json={
                "url": "https://www.youtube.com/watch?v=chancreate1",
                "title": "Channel creation test",
                "new_channel_name": "OpenAI",
                "new_playlist_title": "DevDay Talks",
            },
            headers=_admin_headers(),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created_channel"] is True
    assert body["created_playlist"] is True
    assert body["channel_name"] == "OpenAI"
    assert body["channel_slug"] == "openai"
    assert body["playlist_title"] == "DevDay Talks"

    # DB state: channel + course + section + video all wired
    ch = db_session.query(Channel).filter_by(slug="openai").one()
    course = db_session.query(Course).filter_by(channel_id=ch.id).one()
    assert course.title == "DevDay Talks"
    video = db_session.query(Video).filter_by(youtube_id="chancreate1").one()
    assert video.section.course_id == course.id
    assert video.section.course.channel_id == ch.id


def test_upload_reuses_channel_by_exact_name(
    admin_client: TestClient, db_session: Session
):
    """Second upload with the same channel name reuses the channel and
    lands in its NEW playlist (explicit title) — no channel duplication."""
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        for i, playlist in enumerate(["P1", "P2"]):
            resp = admin_client.post(
                "/api/admin/videos/youtube",
                json={
                    "url": f"https://www.youtube.com/watch?v=reusech{i:04d}",
                    "title": f"Reuse test {i}",
                    "new_channel_name": "OpenAI",
                    "new_playlist_title": playlist,
                },
                headers=_admin_headers(),
            )
            assert resp.status_code == 200, resp.text

    assert db_session.query(Channel).filter_by(name="OpenAI").count() == 1
    assert db_session.query(Course).filter(
        Course.channel_id.is_not(None)
    ).count() == 2


def test_upload_blank_playlist_lands_in_latest(
    admin_client: TestClient, db_session: Session
):
    """Channel picked + blank playlist title → newest playlist's section."""
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    _mk_playlist(db_session, ch, "Existing Playlist")
    db_session.commit()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        resp = admin_client.post(
            "/api/admin/videos/youtube",
            json={
                "url": "https://www.youtube.com/watch?v=blankplist1",
                "title": "Blank playlist test",
                "channel_id": ch.id,
            },
            headers=_admin_headers(),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created_channel"] is False
    assert body["created_playlist"] is False
    assert body["playlist_title"] == "Existing Playlist"

    video = db_session.query(Video).filter_by(youtube_id="blankplist1").one()
    course = db_session.get(Course, video.section.course_id)
    assert course.title == "Existing Playlist"


def test_upload_without_channel_is_personal_flow(
    admin_client: TestClient, db_session: Session
):
    """No channel fields → exactly the pre-channel behavior: section
    resolution via the personal section_picker."""
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        resp = admin_client.post(
            "/api/admin/videos/youtube",
            json={
                "url": "https://www.youtube.com/watch?v=persnlflow1",
                "title": "Personal flow test",
            },
            headers=_admin_headers(),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel_name"] is None
    video = db_session.query(Video).filter_by(youtube_id="persnlflow1").one()
    course = db_session.get(Course, video.section.course_id)
    assert course.channel_id is None  # personal course


def test_upload_bad_channel_id_400(
    admin_client: TestClient, db_session: Session
):
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        resp = admin_client.post(
            "/api/admin/videos/youtube",
            json={
                "url": "https://www.youtube.com/watch?v=badchannel1",
                "title": "Bad channel test",
                "channel_id": "does-not-exist",
            },
            headers=_admin_headers(),
        )
    assert resp.status_code == 400


# ── Browse pages ─────────────────────────────────────────────────────────


def test_catalog_page_lists_channels(client: TestClient, db_session: Session):
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    ch2 = _mk_channel(db_session, "Anthropic", slug="anthropic")
    course, sec = _mk_playlist(db_session, ch, "Talks")
    _mk_video(db_session, sec, "catpage0001")
    # A channel with no playlists must NOT render
    db_session.commit()

    resp = client.get("/catalog")
    assert resp.status_code == 200
    assert "OpenAI" in resp.text
    assert "1 playlist" in resp.text
    assert "1 video" in resp.text
    # Empty channel omitted
    assert "Anthropic" not in resp.text


def test_channel_page_shows_playlist_cards(client: TestClient, db_session: Session):
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    course, sec = _mk_playlist(db_session, ch, "DevDay Talks")
    _mk_video(db_session, sec, "chpage00001")
    _mk_video(db_session, sec, "chpage00002")
    db_session.commit()

    resp = client.get("/channel/openai")
    assert resp.status_code == 200
    assert "DevDay Talks" in resp.text
    assert "2 videos" in resp.text
    # Cover = first video's thumbnail
    assert "https://img.example.com/chpage00001.jpg" in resp.text
    # Link to the playlist page
    assert f"/channel/openai/playlist/{course.id}" in resp.text


def test_channel_playlist_page_lists_videos(client: TestClient, db_session: Session):
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    course, sec = _mk_playlist(db_session, ch, "DevDay Talks")
    v1 = _mk_video(db_session, sec, "plpage00001")
    _mk_video(db_session, sec, "plpage00002")
    db_session.commit()

    resp = client.get(f"/channel/openai/playlist/{course.id}")
    assert resp.status_code == 200
    assert "Video plpage00001" in resp.text
    assert "Video plpage00002" in resp.text
    assert f"/video/{v1.id}" in resp.text
    assert "2 videos" in resp.text


def test_channel_playlist_404_for_wrong_channel(
    client: TestClient, db_session: Session
):
    """A course from ANOTHER channel (or personal) must 404 under this
    channel's URL — playlist existence isn't leaked cross-channel."""
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    other = _mk_channel(db_session, "Anthropic", slug="anthropic")
    course, sec = _mk_playlist(db_session, other, "Anthropic Talks")
    _mk_video(db_session, sec, "crossch00001")
    db_session.commit()

    resp = client.get(f"/channel/openai/playlist/{course.id}")
    assert resp.status_code == 404


def test_channel_unknown_slug_404(client: TestClient, db_session: Session):
    resp = client.get("/channel/does-not-exist")
    assert resp.status_code == 404


# ── Visibility (no tier-content oracle) ──────────────────────────────────


def test_free_user_does_not_see_paid_only_content(
    client: TestClient, db_session: Session
):
    """FREE: PAID_ONLY videos don't appear in channel/playlist cards,
    don't count toward the video count, and the playlist page 404s
    when ALL its videos are PAID_ONLY."""
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    pub_course, pub_sec = _mk_playlist(db_session, ch, "Public Talks")
    _mk_video(db_session, pub_sec, "freepub0001")  # PUBLIC
    paid_course, paid_sec = _mk_playlist(db_session, ch, "Paid Talks")
    _mk_video(db_session, paid_sec, "freepaid001", visibility=1)  # PAID_ONLY
    db_session.commit()

    # Channel page: only the public playlist appears
    resp = client.get("/channel/openai")
    assert resp.status_code == 200
    assert "Public Talks" in resp.text
    assert "Paid Talks" not in resp.text

    # The all-PAID playlist page 404s for FREE
    resp = client.get(f"/channel/openai/playlist/{paid_course.id}")
    assert resp.status_code == 404

    # Catalog counts reflect only visible content
    resp = client.get("/catalog")
    assert "1 playlist" in resp.text
    assert "1 video" in resp.text


def test_signed_out_user_sees_public_only(
    client: TestClient, db_session: Session
):
    """Anonymous visitors (role None) get the PUBLIC tier — same as FREE."""
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    course, sec = _mk_playlist(db_session, ch, "Talks")
    _mk_video(db_session, sec, "anonpub0001")
    db_session.commit()

    resp = client.get("/catalog")
    assert resp.status_code == 200
    assert "OpenAI" in resp.text


# ── Admin upload page renders pickers ────────────────────────────────────


def test_admin_upload_page_has_channel_pickers(
    admin_client: TestClient, db_session: Session
):
    from unittest.mock import patch
    from tests.test_admin_router import _admin_token

    _promote_admin(db_session)
    ch = _mk_channel(db_session, "OpenAI", slug="openai")
    db_session.commit()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_admin_token(),
    ):
        resp = admin_client.get("/admin/upload")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="channel_id"' in html
    assert 'id="new_channel_name"' in html
    assert 'id="new_playlist_title"' in html
    assert 'id="personal-section-picker"' in html
    # Existing channels populate the dropdown
    assert '<option value="' + ch.id + '">OpenAI</option>' in html
    # The channel-mode toggle JS exists
    assert "personal.classList.add" in html


# ── Watch-page breadcrumb shows the channel path ─────────────────────────


def test_video_page_breadcrumb_shows_channel_path(
    client: TestClient, db_session: Session
):
    """2026-09-08: channel videos' breadcrumb must be
    Catalog → Channel → Playlist → Title (so users can navigate
    back into the channel tree from the watch page — the missing
    breadcrumb was the 'why don't I see my channel' confusion)."""
    ch = _mk_channel(db_session, "Claude", slug="claude")
    course, sec = _mk_playlist(db_session, ch, "Claude Code 101")
    v = _mk_video(db_session, sec, "bcumb000001")
    db_session.commit()

    resp = client.get(f"/video/{v.id}")
    assert resp.status_code == 200
    html = resp.text
    assert 'href="/catalog"' in html
    assert 'href="/channel/claude"' in html
    assert f'href="/channel/claude/playlist/{course.id}"' in html
    assert "Claude Code 101" in html


def test_video_page_breadcrumb_personal_course_unchanged(
    client: TestClient, db_session: Session
):
    """Personal uploads keep the old Dashboard → Course breadcrumb —
    no channel link for channel-less courses."""
    from app.models import Course as _C, Section as _S

    course = _C(title="My Personal Course", user_id="uid-admin")
    db_session.add(course)
    db_session.flush()
    sec = _S(title="Week 1", course_id=course.id, order_index=0)
    db_session.add(sec)
    db_session.flush()
    v = _mk_video(db_session, sec, "persnlbcumb1")
    db_session.commit()

    resp = client.get(f"/video/{v.id}")
    assert resp.status_code == 200
    html = resp.text
    # Scope to the breadcrumb <header> region — the sidebar's new
    # "📚 Catalog" nav link legitimately contains href="/catalog"
    # on EVERY page, so a whole-page assertion would always fail.
    import re as _re
    m = _re.search(r"<header[^>]*>(.*?)</header>", html, _re.DOTALL)
    assert m, "header (breadcrumb region) not found in page"
    crumb = m.group(1)
    assert 'href="/catalog"' not in crumb
    assert "/channel/" not in crumb
    assert 'href="/course/' + course.id + '"' in crumb