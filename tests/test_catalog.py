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

from app.auth.admin import ensure_user_row
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
    with _mock_verify_token("uid-admin", "admin@x.com"):
        with TestClient(app) as c:
            response = c.get("/", headers={"Authorization": "Bearer fake"})
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
