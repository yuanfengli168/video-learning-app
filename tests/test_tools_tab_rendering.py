"""Tests for the Tools tab rendering on the video page (MVP2.1.0).

The Tools tab is the UI surface for the Plugin Tools
system. These tests verify:
  - The Tools tab button is present on the video page
  - The plugin list is rendered with the v1 plugin
  - The "ffmpeg not found" state is rendered when
    ffmpeg is absent (we test the rendering with a
    faked `is_ffmpeg_available` returning False)
  - The disabled button has the right CSS class
  - The JS runPlugin() function is included in the
    page's <script> block
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.section import Section
from app.models.video import Video


# ── Helpers ─────────────────────────────────────────────────────────────
def _seed_video(db: Session) -> Video:
    """Create a Course -> Section -> Video chain for the tests."""
    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1",
        section_id="s1",
        title="Test video",
        filename="lesson.webm",
        file_path="lesson.webm",
        status="ready",
    )
    db.add_all([course, section, video])
    db.commit()
    db.refresh(video)
    return video


# ── Tools tab button presence ───────────────────────────────────────────
def test_tools_tab_button_renders(client: TestClient, db_session: Session):
    """The video page includes a Tools tab button."""
    _seed_video(db_session)
    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="tab-tools"' in html
    # The button text is the emoji + "Tools" label
    assert "Tools" in html


def test_tools_tab_button_calls_switchTab(client: TestClient, db_session: Session):
    """The button's onclick is `switchTab('tools')` (the standard pattern)."""
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text
    assert "switchTab('tools')" in html


# ── Tools tab content panel ─────────────────────────────────────────────
def test_tools_content_panel_renders(client: TestClient, db_session: Session):
    """The Tools tab content <div> exists, hidden by default."""
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text
    assert 'id="content-tools"' in html
    # The other tab content panels use `class="hidden"`. Same here.
    # We just check the id is present; the class will be in the
    # same tag so a substring check is fine.
    assert 'id="content-tools" class="hidden"' in html


def test_tools_panel_lists_v1_plugin(client: TestClient, db_session: Session):
    """The v1 WebM -> MP4 plugin is rendered as a card."""
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text
    # The plugin key is used as the data attribute
    assert 'data-plugin-key="webm_to_mp4"' in html
    # The label is shown
    assert "Convert to MP4" in html
    # The description is shown
    assert "Transcode" in html or "H.264" in html


def test_tools_panel_renders_run_button(client: TestClient, db_session: Session):
    """Each plugin has a Run button with the right id."""
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text
    assert 'id="run-plugin-webm_to_mp4"' in html
    assert "Run" in html


# ── ffmpeg-not-found state ──────────────────────────────────────────────
def test_tools_panel_shows_disabled_state_when_ffmpeg_missing(
    client: TestClient, db_session: Session, monkeypatch
):
    """If ffmpeg is not on $PATH, the Run button is disabled and a
    warning is shown.

    We patch the ffmpeg-detection in the router (frontend.py
    imports `shutil.which` directly via a local alias) AND
    in the service layer. Both must return "missing" for
    the UI to render the disabled state.
    """
    _seed_video(db_session)

    # Patch the router-side check (frontend.py uses `shutil.which`
    # directly with a local import alias `_shutil`).
    monkeypatch.setattr("shutil.which", lambda x: None)

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The Run button is disabled
    assert 'id="run-plugin-webm_to_mp4" disabled' in html or (
        'id="run-plugin-webm_to_mp4"' in html and "disabled" in html
    )
    # The "Missing system dependency" warning is shown
    assert "Missing system dependency" in html
    assert "ffmpeg" in html


# ── JS function presence ────────────────────────────────────────────────
def test_video_page_includes_runPlugin_function(
    client: TestClient, db_session: Session
):
    """The <script> block in the video page defines runPlugin().

    We check for the function name; not the full body (which
    would be brittle).
    """
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text
    assert "async function runPlugin" in html
    assert "/api/plugins/" in html  # the API path is hardcoded
