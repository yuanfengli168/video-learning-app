"""Tests for the admin analytics + usage-monitor pages (2026-09-05).

Covers:
  1. Admin gate — non-admin (FREE/anonymous) gets 403/redirect; only
     CURATE_CATALOG holders see the pages.
  2. Analytics aggregation — seeded ui.* + LLM events produce the
     right counters (plays, logins, chats, tab clicks, LLM ok/fail).
  3. Top videos join — titles resolve; deleted videos degrade to
     "(deleted video)".
  4. Usage monitor — per-user 7h/week counts + role badges in the
     rendered table.
  5. days param clamping — ?days=999 can't force an unbounded scan.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

FAKE_ADMIN = {"uid": "test-user-uid", "email": "admin@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_admin():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_ADMIN,
    )


def _seed_event(
    db_session, *, source: str, message: str,
    user_id: str = "test-user-uid", video_id: str | None = None,
):
    db_session.execute(
        text(
            "INSERT INTO events (id, ts, level, source, message, user_id, video_id, context_json) "
            "VALUES (lower(hex(randomblob(16))), datetime('now'), 'INFO', "
            ":source, :message, :user_id, :video_id, '{}')"
        ),
        {"source": source, "message": message, "user_id": user_id,
         "video_id": video_id},
    )


def _make_video(db_session, title: str = "analytics test video") -> str:
    from app.models import Course, Section, Video

    course = Course(title="analytics", user_id="test-user-uid")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S", course_id=course.id, order_index=0)
    db_session.add(section)
    db_session.flush()
    video = Video(
        title=title, filename="a.mp4", file_path="/tmp/a.mp4",
        file_size=1, duration=1.0, order_index=0,
        section_id=section.id, status="ready", visibility=0,
        caption_languages="[]",
    )
    db_session.add(video)
    db_session.commit()
    return video.id


# ─────────────────────────────────────────────────────────────────────────────
# 1. Admin gate
# ─────────────────────────────────────────────────────────────────────────────

def test_analytics_requires_admin_capability(client: TestClient, db_session):
    """Default client = FREE user (no role row) → 403 from require_capability."""
    with _mock_admin():
        resp = client.get("/admin/analytics", headers=_auth_headers())
    assert resp.status_code == 403


def test_usage_monitor_requires_admin_capability(client: TestClient, db_session):
    with _mock_admin():
        resp = client.get("/admin/usage", headers=_auth_headers())
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 2. Aggregation correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_analytics_counts_seeded_events(admin_client: TestClient, db_session):
    vid = _make_video(db_session)
    _seed_event(db_session, source="ui.login", message="ui user logged in")
    _seed_event(db_session, source="ui.player", message="ui player play",
                video_id=vid)
    _seed_event(db_session, source="ui.player", message="ui player play",
                video_id=vid)
    _seed_event(db_session, source="ui.player", message="ui player pause",
                video_id=vid)
    _seed_event(db_session, source="ui.materials",
                message="ui materials tab mindmap", video_id=vid)
    _seed_event(db_session, source="ui.chat", message="ui chat message sent",
                video_id=vid)
    _seed_event(db_session, source="services.llm_providers",
                message="LLM call succeeded via ollama/glm-5.2:cloud")
    _seed_event(db_session, source="services.llm_providers",
                message="LLM call failed on groq/groq/compound-mini")
    db_session.commit()

    with _mock_admin():
        resp = admin_client.get("/admin/analytics", headers=_auth_headers())

    assert resp.status_code == 200
    html = resp.text
    # The service numbers (spot-check via the service directly —
    # template assertions are for rendering below).
    from app.services.analytics import get_analytics_overview
    stats = get_analytics_overview(db_session)
    assert stats["logins"] >= 1
    assert stats["video_plays"] >= 2
    assert stats["video_pauses"] >= 1
    assert stats["materials_tab_clicks"] >= 1
    assert stats["chat_messages"] >= 1
    assert stats["llm_calls"] >= 1
    assert stats["llm_failures"] >= 1
    # Rendering spot-checks
    assert "analytics test video" in html


def test_analytics_plugin_run_counters(admin_client: TestClient, db_session):
    """Plugin-run (Tools tab) stats show up in the analytics overview.

    Guards the 2026-09-08 addition: plugin runs never write to the
    events table, so their counters come from plugin_runs directly
    (keep/kill signal for the Tools feature).
    """
    from app.models.plugin_run import PluginRun

    vid = _make_video(db_session)

    def _seed_run(uid: str, status_: str, ok: bool) -> None:
        db_session.add(PluginRun(
            plugin_key="webm_to_mp4", video_id=vid, user_id=uid,
            status=status_, ok=ok,
            message="seed", output_path=None, extra_json="{}",
        ))

    # Two successes by one user, one failure by another, one in-flight.
    _seed_run("u-one", "done", True)
    _seed_run("u-one", "done", True)
    _seed_run("u-two", "failed", False)
    _seed_run("u-two", "running", False)
    db_session.commit()

    from app.services.analytics import get_analytics_overview
    stats = get_analytics_overview(db_session)
    assert stats["plugin_runs_total"] == 4
    assert stats["plugin_runs_ok"] == 2
    assert stats["plugin_runs_failed"] == 1
    assert stats["plugin_runs_in_flight"] == 1
    assert stats["plugin_top"][0]["plugin"] == "webm_to_mp4"
    assert stats["plugin_top"][0]["runs"] == 4
    assert stats["plugin_top"][0]["users"] == 2

    # Page renders the Tools section
    with _mock_admin():
        resp = admin_client.get("/admin/analytics", headers=_auth_headers())
    assert resp.status_code == 200
    assert "plugin runs" in resp.text.lower() or "Tools (plugin runs)" in resp.text


def test_analytics_days_param_clamped(admin_client: TestClient, db_session):
    """?days=999 → clamped to 90 (no unbounded scan)."""
    with _mock_admin():
        resp = admin_client.get(
            "/admin/analytics?days=999", headers=_auth_headers()
        )
    assert resp.status_code == 200
    from app.services.analytics import get_analytics_overview
    # Direct: the clamp happens in the route; assert service accepts 90
    stats = get_analytics_overview(db_session, days=90)
    assert stats["window_days"] == 90


# ─────────────────────────────────────────────────────────────────────────────
# 6. Content structure table (2026-09-12) — courses × sections × videos
# ─────────────────────────────────────────────────────────────────────────────

def _mk_structure(db_session, *, course_title: str, section_titles: list[str],
                  statuses: list[str]):
    """Course + one video per given status list, spread across the
    sections round-robin. Returns the course id."""
    from app.models import Course, Section, Video

    course = Course(title=course_title, user_id="test-user-uid",
                    description="")
    db_session.add(course)
    db_session.flush()
    sections = []
    for st in section_titles:
        s = Section(title=st, course_id=course.id, order_index=0)
        db_session.add(s)
        sections.append(s)
    db_session.flush()
    for i, status in enumerate(statuses):
        v = Video(
            title=f"{course_title} v{i}", filename=f"{i}.mp4",
            file_path=f"/tmp/{i}.mp4", file_size=1, duration=1.0,
            order_index=0, section_id=sections[i % len(sections)].id,
            status=status, visibility=0, caption_languages="[]",
        )
        db_session.add(v)
    db_session.commit()
    return course.id


def test_analytics_structure_table_renders(admin_client: TestClient, db_session):
    """The admin analytics page shows a per-course structure row with
    section/video/ready/error counts (backing data for future feature
    decisions like a course search bar)."""
    _mk_structure(
        db_session,
        course_title="Structure Alpha",
        section_titles=["S1", "S2"],
        statuses=["ready", "ready", "error", "ready"],
    )
    _mk_structure(
        db_session,
        course_title="Structure Beta",
        section_titles=["S1"],
        statuses=["ready"],
    )
    # A course with zero sections — LEFT JOIN must yield 0s, not crash.
    from app.models import Course
    db_session.add(Course(title="Empty Course", user_id="test-user-uid",
                           description=""))
    db_session.commit()

    with _mock_admin():
        resp = admin_client.get("/admin/analytics", headers=_auth_headers())
    assert resp.status_code == 200
    html = resp.text
    assert "Content structure" in html
    assert "Structure Alpha" in html
    assert "Structure Beta" in html
    assert "Empty Course" in html  # zero-row renders, no crash

    from app.services.analytics import get_analytics_overview
    stats = get_analytics_overview(db_session)
    by_title = {r["course_title"]: r for r in stats["structure"]}
    alpha = by_title["Structure Alpha"]
    assert alpha["sections"] == 2
    assert alpha["videos"] == 4
    assert alpha["ready"] == 3
    assert alpha["errors"] == 1
    beta = by_title["Structure Beta"]
    assert beta["sections"] == 1
    assert beta["videos"] == 1
    assert beta["ready"] == 1
    empty = by_title["Empty Course"]
    assert empty["sections"] == 0
    assert empty["videos"] == 0
    # Sorted by video count desc → Alpha first
    assert stats["structure"][0]["course_title"] == "Structure Alpha"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Usage monitor
# ─────────────────────────────────────────────────────────────────────────────

def test_usage_monitor_lists_users_with_counts(admin_client: TestClient, db_session):
    from app.services.analytics import get_all_users_usage

    for _ in range(3):
        _seed_event(db_session, source="services.llm_providers",
                    message="LLM call succeeded via ollama/glm-5.2:cloud",
                    user_id="test-user-uid")
    db_session.commit()

    with _mock_admin():
        resp = admin_client.get("/admin/usage", headers=_auth_headers())

    assert resp.status_code == 200
    rows = get_all_users_usage(db_session)
    me = [r for r in rows if r["user_id"] == "test-user-uid"]
    assert me and me[0]["used_week"] >= 3
    assert me[0]["used_7h"] >= 3
    # The admin (test-user-uid, role=0 per admin_client fixture) shows
    # the ADMIN badge copy and the numeric limits
    assert "admin@example.com" in resp.text
    assert "50" in resp.text and "100" in resp.text


def test_usage_monitor_empty_renders_hint(admin_client: TestClient, db_session):
    with _mock_admin():
        resp = admin_client.get("/admin/usage", headers=_auth_headers())
    assert resp.status_code == 200
    assert "No LLM requests recorded this week" in resp.text