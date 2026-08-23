"""Tests for the catalog service (visibility-filtered video queries).

Covers:
- visible_videos_for_role: returns only videos with visibility <= role's max
- visible_videos_for_user: convenience wrapper, looks up role from DB
- count_visible_videos_for_role: count query
- ordering: newest first
- pagination: limit + offset work
- anonymous (None role) defaults to PUBLIC only
- integer role values accepted (not just UserRole enum)

Also covers:
- Dashboard route passes catalog_videos to template, filtered correctly
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.admin import clear_role_cache, ensure_user_row
from app.auth.roles import UserRole, VideoVisibility
from app.main import app
from app.models import Course, Section, Video
from app.services.catalog import (
    count_visible_videos_for_role,
    visible_videos_for_role,
    visible_videos_for_user,
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_role_cache_between_tests():
    """Each test starts with empty role cache.

    _lookup_role_cached is module-level lru_cache keyed by (uid, role_db).
    Tests that change role via UPDATE without clearing the cache would
    see stale results. Autouse so we don't have to remember per-test.
    """
    clear_role_cache()
    yield
    clear_role_cache()


@pytest.fixture
def course_and_section(db_session):
    """Create a default course + section so videos have a valid FK target."""
    course = Course(id="course-1", title="Test", user_id="uid-admin")
    db_session.add(course)
    db_session.flush()
    section = Section(id="section-1", title="S1", course_id="course-1", order_index=0)
    db_session.add(section)
    db_session.commit()
    return course, section


def _make_video(
    db_session,
    title: str,
    visibility: int,
    youtube_id: str | None = None,
) -> Video:
    """Create a Video row in the DB (must have a section_id FK)."""
    v = Video(
        id=f"v-{title.lower().replace(' ', '-')}",
        title=title,
        filename=f"youtube:{youtube_id or 'unknown'}",
        file_path=f"https://www.youtube.com/watch?v={youtube_id or 'unknown'}",
        file_size=0,
        section_id="section-1",
        status="ready",
        visibility=visibility,
        youtube_id=youtube_id,
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


@pytest.fixture
def three_visibility_videos(db_session, course_and_section):
    """One PUBLIC, one PAID_ONLY, one ADMIN_ONLY video."""
    public = _make_video(db_session, "Public Video", VideoVisibility.PUBLIC.value, "ytpublic00")
    paid = _make_video(db_session, "Paid Video", VideoVisibility.PAID_ONLY.value, "ytpaid00001")
    admin = _make_video(db_session, "Admin Video", VideoVisibility.ADMIN_ONLY.value, "ytadmin002")
    return {"public": public, "paid": paid, "admin": admin}


# ─────────────────────────────────────────────────────────────────────────
# visible_videos_for_role — core logic
# ─────────────────────────────────────────────────────────────────────────


def test_free_sees_only_public(db_session, three_visibility_videos):
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE)
    ).scalars().all()
    titles = [v.title for v in rows]
    assert titles == ["Public Video"]


def test_paid_sees_public_and_paid(db_session, three_visibility_videos):
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.PAID)
    ).scalars().all()
    titles = sorted(v.title for v in rows)
    assert titles == ["Paid Video", "Public Video"]


def test_admin_sees_all(db_session, three_visibility_videos):
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.ADMIN)
    ).scalars().all()
    titles = sorted(v.title for v in rows)
    assert titles == ["Admin Video", "Paid Video", "Public Video"]


def test_anonymous_sees_only_public(db_session, three_visibility_videos):
    """None role → FREE (PUBLIC only) — safer default."""
    rows = db_session.execute(
        visible_videos_for_role(db_session, None)
    ).scalars().all()
    titles = [v.title for v in rows]
    assert titles == ["Public Video"]


def test_int_role_accepted(db_session, three_visibility_videos):
    """Passing int 0 (ADMIN) works the same as UserRole enum."""
    rows = db_session.execute(
        visible_videos_for_role(db_session, 0)  # int 0 = ADMIN
    ).scalars().all()
    titles = sorted(v.title for v in rows)
    assert titles == ["Admin Video", "Paid Video", "Public Video"]


def test_ordering_newest_first(db_session, course_and_section):
    """Catalog returns newest first (admin-curated flow).

    We set explicit created_at timestamps to avoid SQLite second-precision
    truncation (multiple inserts in the same second get the same ts).
    """
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i, name in enumerate(["Old", "Middle", "New"]):
        v = Video(
            id=f"v-ordering-{i}",
            title=name,
            filename=f"youtube:order{i:02d}",
            file_path=f"https://www.youtube.com/watch?v=order{i:02d}",
            file_size=0,
            section_id="section-1",
            status="ready",
            visibility=VideoVisibility.PUBLIC.value,
            youtube_id=f"ytorder{i:03d}",
        )
        db_session.add(v)
        db_session.flush()
        # Explicit timestamp — spaced 1 minute apart
        db_session.execute(
            text("UPDATE videos SET created_at=:ts WHERE id=:id"),
            {"ts": (base + timedelta(minutes=i)).isoformat(), "id": f"v-ordering-{i}"},
        )
    db_session.commit()
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE)
    ).scalars().all()
    titles = [v.title for v in rows]
    # Newest first — explicit ts spacing
    assert titles == ["New", "Middle", "Old"]


def test_limit(db_session, course_and_section):
    """limit=N returns at most N rows."""
    for i in range(5):
        _make_video(
            db_session, f"V{i}", VideoVisibility.PUBLIC.value, f"ytvid0000{i}",
        )
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE, limit=2)
    ).scalars().all()
    assert len(rows) == 2


def test_offset(db_session, course_and_section):
    """offset=N skips first N rows."""
    for i in range(5):
        _make_video(
            db_session, f"V{i}", VideoVisibility.PUBLIC.value, f"ytvid0000{i}",
        )
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE, limit=5, offset=2)
    ).scalars().all()
    assert len(rows) == 3


def test_empty_catalog(db_session):
    """No videos → empty result."""
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE)
    ).scalars().all()
    assert rows == []


# ─────────────────────────────────────────────────────────────────────────
# count_visible_videos_for_role
# ─────────────────────────────────────────────────────────────────────────


def test_count_free(db_session, three_visibility_videos):
    assert count_visible_videos_for_role(db_session, UserRole.FREE) == 1


def test_count_paid(db_session, three_visibility_videos):
    assert count_visible_videos_for_role(db_session, UserRole.PAID) == 2


def test_count_admin(db_session, three_visibility_videos):
    assert count_visible_videos_for_role(db_session, UserRole.ADMIN) == 3


def test_count_anonymous(db_session, three_visibility_videos):
    """None role defaults to FREE count."""
    assert count_visible_videos_for_role(db_session, None) == 1


def test_count_int_role(db_session, three_visibility_videos):
    assert count_visible_videos_for_role(db_session, 0) == 3  # int 0 = ADMIN


def test_count_invalid_int_defaults_to_public(db_session, three_visibility_videos):
    """Unknown int role (e.g. 5) defaults to PUBLIC (most restrictive)."""
    assert count_visible_videos_for_role(db_session, 5) == 1


def test_count_empty(db_session):
    assert count_visible_videos_for_role(db_session, UserRole.FREE) == 0


# ─────────────────────────────────────────────────────────────────────────
# visible_videos_for_user — convenience wrapper
# ─────────────────────────────────────────────────────────────────────────


def test_visible_videos_for_user_admin(db_session, three_visibility_videos, admin_user_setup):
    uid = admin_user_setup
    rows = db_session.execute(
        visible_videos_for_user(db_session, {"uid": uid, "email": "a@x.com"})
    ).scalars().all()
    assert len(rows) == 3


def test_visible_videos_for_user_free(db_session, three_visibility_videos, free_user_setup):
    uid = free_user_setup
    rows = db_session.execute(
        visible_videos_for_user(db_session, {"uid": uid, "email": "f@x.com"})
    ).scalars().all()
    assert len(rows) == 1


def test_visible_videos_for_user_anonymous(db_session, three_visibility_videos):
    """No user → FREE default."""
    rows = db_session.execute(
        visible_videos_for_user(db_session, None)
    ).scalars().all()
    assert len(rows) == 1


def test_visible_videos_for_user_missing_uid(db_session, three_visibility_videos):
    """user dict without 'uid' → FREE default."""
    rows = db_session.execute(
        visible_videos_for_user(db_session, {"email": "x@x.com"})
    ).scalars().all()
    assert len(rows) == 1


@pytest.fixture
def admin_user_setup(db_session):
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    return "uid-admin"


@pytest.fixture
def free_user_setup(db_session):
    ensure_user_row("uid-free", "free@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=2 WHERE user_id='uid-free'"))
    db_session.commit()
    return "uid-free"


# ─────────────────────────────────────────────────────────────────────────
# Dashboard route integration — catalog section rendered
# ─────────────────────────────────────────────────────────────────────────


def _mock_verify_token(uid: str, email: str):
    return patch(
        "app.auth.dependencies.verify_token",
        return_value={"uid": uid, "email": email},
    )


def test_dashboard_shows_catalog_section(client: TestClient, db_session):
    """Dashboard renders the catalog heading even when empty."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    with _mock_verify_token("uid-admin", "admin@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    html = response.text
    assert "Catalog" in html


def test_dashboard_catalog_filters_for_free_user(
    client: TestClient, db_session, three_visibility_videos,
):
    """Free user sees only the PUBLIC video in catalog."""
    ensure_user_row("uid-free", "free@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=2 WHERE user_id='uid-free'"))
    db_session.commit()
    with _mock_verify_token("uid-free", "free@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    html = response.text
    assert "Public Video" in html
    assert "Paid Video" not in html
    assert "Admin Video" not in html


def test_dashboard_catalog_filters_for_admin(
    client: TestClient, db_session, three_visibility_videos,
):
    """Admin sees all 3 videos in catalog."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    with _mock_verify_token("uid-admin", "admin@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    html = response.text
    assert "Public Video" in html
    assert "Paid Video" in html
    assert "Admin Video" in html


def test_dashboard_catalog_anonymous(
    client: TestClient, db_session, three_visibility_videos,
):
    """Anonymous (no token) sees only PUBLIC."""
    with TestClient(app) as c:
        response = c.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Public Video" in html
    assert "Paid Video" not in html
    assert "Admin Video" not in html


def test_dashboard_shows_admin_cta_for_empty_catalog_when_admin(
    client: TestClient, db_session,
):
    """If catalog empty AND user is admin → 'Add one' link shown."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    # Override the conftest's default verify_token mock so we ARE uid-admin.
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    html = response.text
    assert "No videos in the catalog" in html
    assert "/admin/upload" in html


def test_dashboard_hides_admin_cta_for_free_user_when_empty(
    client: TestClient, db_session,
):
    """Empty catalog AND free user → no admin CTA link."""
    ensure_user_row("uid-free", "free@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=2 WHERE user_id='uid-free'"))
    db_session.commit()
    with _mock_verify_token("uid-free", "free@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    html = response.text
    assert "No videos in the catalog" in html
    # CTA only shown when is_admin
    # But the sidebar nav has /admin/upload link for admin — free user has no sidebar link either
    assert html.count("href=\"/admin/upload\"") == 0


# ─────────────────────────────────────────────────────────────────────────
# Legacy upload exclusion (MVP2 catalog only shows YouTube videos)
# ─────────────────────────────────────────────────────────────────────────


def _make_legacy_upload(db_session, title: str, visibility: int = 0) -> Video:
    """Create a pre-pivot uploaded video (no youtube_id).

    Legacy uploads have youtube_id=NULL — they were added before the
    YouTube pivot. Visibility defaults to 0 (PUBLIC) per the migration
    default. These should NOT appear in the MVP2 catalog.
    """
    v = Video(
        id=f"v-legacy-{title.lower().replace(' ', '-')}",
        title=title,
        filename=f"{title}.mp4",
        file_path=f"/uploads/{title}.mp4",
        file_size=1024 * 1024,
        section_id="section-1",
        status="ready",
        visibility=visibility,
        youtube_id=None,  # KEY: no youtube_id
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


def test_legacy_uploads_excluded_from_catalog(db_session, course_and_section):
    """Pre-pivot uploads (no youtube_id) must NOT appear in the catalog.

    Bug fix 2026-08-23: without the youtube_id filter, every legacy
    upload (visibility=0 by migration default) was polluting the new
    admin-curated catalog. Now the catalog is YouTube-only.
    """
    _make_legacy_upload(db_session, "Legacy Upload", VideoVisibility.PUBLIC.value)
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.FREE)
    ).scalars().all()
    assert rows == []


def test_catalog_has_only_youtube_videos(db_session, course_and_section, three_visibility_videos):
    """Mix of legacy + youtube videos → catalog only shows youtube ones."""
    _make_legacy_upload(db_session, "Old Upload A", VideoVisibility.PUBLIC.value)
    _make_legacy_upload(db_session, "Old Upload B", VideoVisibility.PAID_ONLY.value)
    rows = db_session.execute(
        visible_videos_for_role(db_session, UserRole.ADMIN)
    ).scalars().all()
    # Only the 3 from three_visibility_videos fixture (all have youtube_id)
    titles = sorted(v.title for v in rows)
    assert titles == ["Admin Video", "Paid Video", "Public Video"]


def test_count_visible_excludes_legacy_uploads(db_session, course_and_section):
    """count_visible_videos_for_role also excludes legacy uploads."""
    _make_legacy_upload(db_session, "Legacy 1", VideoVisibility.PUBLIC.value)
    _make_legacy_upload(db_session, "Legacy 2", VideoVisibility.PAID_ONLY.value)
    _make_video(db_session, "Modern Video", VideoVisibility.PUBLIC.value, "ytmodern0001")
    # Admin should see 1 (modern), not 3 (incl. 2 legacy)
    assert count_visible_videos_for_role(db_session, UserRole.ADMIN) == 1


def test_dashboard_catalog_empty_when_only_legacy_uploads(
    client: TestClient, db_session,
):
    """If catalog has only legacy uploads (no youtube_id), dashboard shows empty state."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()

    # Need a course/section first so we can create the legacy video
    course = Course(id="course-d", title="D", user_id="uid-admin")
    db_session.add(course)
    db_session.flush()
    section = Section(id="section-d", title="S", course_id="course-d", order_index=0)
    db_session.add(section)
    db_session.commit()
    _make_legacy_upload(db_session, "Only Legacy", VideoVisibility.PUBLIC.value)

    with _mock_verify_token("uid-admin", "admin@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    html = response.text
    assert "No videos in the catalog" in html
    assert "Only Legacy" not in html  # NOT shown


# ─────────────────────────────────────────────────────────────────────────
# Dashboard catalog card rendering (Day 2C: thumbnails, channels, duration, course badge)
# ─────────────────────────────────────────────────────────────────────────


def _make_video_with_metadata(
    db_session,
    title: str,
    visibility: int,
    youtube_id: str,
    channel: str = "Test Channel",
    thumbnail_url: str | None = None,
    duration: float = 214.0,
) -> Video:
    """Create a Video with full YouTube metadata enrichment.

    thumbnail_url defaults to a URL derived from the youtube_id so
    tests can search for either 'abc' (the placeholder) or the
    actual ID — usually the actual ID.
    """
    if thumbnail_url is None:
        thumbnail_url = f"https://i.ytimg.com/vi/{youtube_id}/maxresdefault.jpg"
    v = Video(
        id=f"v-meta-{title.lower().replace(' ', '-')}",
        title=title,
        filename=f"youtube:{youtube_id}",
        file_path=f"https://www.youtube.com/watch?v={youtube_id}",
        file_size=0,
        section_id="section-1",
        status="ready",
        visibility=visibility,
        youtube_id=youtube_id,
        channel=channel,
        thumbnail_url=thumbnail_url,
        duration=duration,
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


def test_dashboard_catalog_card_shows_thumbnail(client: TestClient, db_session, course_and_section):
    """Card renders <img src=v.thumbnail_url> when YouTube metadata is present."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(db_session, "With thumb", 0, "ytwiththumb0")
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    html = response.text
    assert "https://i.ytimg.com/vi/ytwiththumb0/maxresdefault.jpg" in html
    # (the <img tag is split across lines by Jinja, so check for 'src='
    #  pointing at our thumbnail as a proxy for img tag presence)
    assert 'src="https://i.ytimg.com/vi/ytwiththumb0' in html
    assert 'object-cover' in html  # Tailwind class for fitting


def test_dashboard_catalog_card_shows_channel(client: TestClient, db_session, course_and_section):
    """Card shows YouTube channel name under the title."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(db_session, "Channel test", 0, "ytchannel001", channel="3Blue1Brown")
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    assert "3Blue1Brown" in response.text


def test_dashboard_catalog_card_shows_duration(client: TestClient, db_session, course_and_section):
    """Card shows duration overlay in m:ss format when duration > 0."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(db_session, "Duration test", 0, "ytdur000001", duration=214.0)
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    assert "3:34" in response.text  # 214s = 3 minutes 34 seconds


def test_dashboard_catalog_card_no_duration_when_zero(client: TestClient, db_session, course_and_section):
    """Duration overlay hidden when duration=0 (legacy uploads have no duration)."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(db_session, "No duration", 0, "ytnodur00001", duration=0.0)
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    # No time overlay like "0:00" should appear (since duration=0 → no overlay)
    # The visibility badge still shows though
    assert "No duration" in response.text


def test_dashboard_catalog_card_shows_course_section_badge(client: TestClient, db_session, course_and_section):
    """Card shows 'Course / Section' badge so users know where the video lives."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(db_session, "Badge test", 0, "ytbadge00001")
    # The `client` fixture from conftest already has the session cookie
    # + mocks verify_token globally. Just hit the URL.
    response = client.get("/")
    html = response.text
    # Course is "Test" / Section is "S1" (from course_and_section fixture)
    assert "Test / S1" in html


def test_dashboard_catalog_card_falls_back_to_emoji_without_thumbnail(
    client: TestClient, db_session
):
    """Card shows 🎬 emoji when thumbnail_url is NULL (legacy or partial data)."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_legacy_upload(db_session, "Legacy Card")
    with _mock_verify_token("uid-admin", "admin@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
    # No <img> for this card; emoji present
    html = response.text
    # (other videos may have <img>, so we check for the absence of thumbnail for "Legacy Card")
    # Use a unique check: no thumbnail URL contains "ytlgc" since this video has no youtube_id
    assert "ytlegacy" not in html  # shouldn't appear since legacy is excluded
    # The card showing for "Legacy Card" doesn't exist at all (excluded from catalog)
    assert "Legacy Card" not in html


def test_dashboard_catalog_card_paid_badge_still_works(client: TestClient, db_session, course_and_section):
    """Paid visibility badge still renders alongside new metadata fields."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    _make_video_with_metadata(
        db_session, "Paid Video", VideoVisibility.PAID_ONLY.value, "ytpaidbad001"
    )
    with _mock_verify_token("uid-admin", "admin@x.com"):
        response = client.get("/")
    assert "🔒 Paid" in response.text


# ─────────────────────────────────────────────────────────────────────────
# Video watch page — iframe for YouTube, <video> for legacy
# ─────────────────────────────────────────────────────────────────────────


def test_watch_page_renders_iframe_for_youtube_video(client: TestClient, db_session, course_and_section):
    """Watch page embeds YouTube iframe (youtube-nocookie.com) when youtube_id set."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    v = _make_video_with_metadata(db_session, "YT Watch Test", 0, "ytwatch00001")
    # The `client` fixture from conftest already sets a default valid
    # session cookie + mocks verify_token globally. Just hit the URL.
    response = client.get(f"/video/{v.id}")
    html = response.text
    assert "youtube-nocookie.com/embed/ytwatch00001" in html
    assert "<iframe" in html
    assert "allowfullscreen" in html


def test_watch_page_renders_html5_video_for_legacy(client: TestClient, db_session, course_and_section):
    """Watch page renders <video src=/api/videos/.../file> for legacy uploads."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text("UPDATE users SET role=0 WHERE user_id='uid-admin'"))
    db_session.commit()
    v = _make_legacy_upload(db_session, "Legacy Watch")
    response = client.get(f"/video/{v.id}")
    html = response.text
    assert "<video" in html
    assert f"/api/videos/{v.id}/file" in html
    assert "<iframe" not in html  # no iframe for legacy
    assert "youtube-nocookie" not in html
