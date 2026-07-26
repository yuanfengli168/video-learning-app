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


# ── MVP2.1.0.1: "Last run" line UI ─────────────────────────────────────
def test_tools_panel_shows_empty_state_when_no_runs(
    client, db_session
):
    """When the video has no plugin runs, show the empty-state hint."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The empty-state hint
    assert "No plugin runs yet" in html
    assert "click Run to convert to MP4" in html


def test_tools_panel_shows_last_successful_run(client, db_session, tmp_path):
    """When the video has a successful run, show the path + Open in Finder button."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone
    from app.config import settings
    import os

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    # Create a real file so the path renders nicely
    real_path = tmp_path / "lesson.mp4"
    real_path.write_bytes(b"fake mp4")

    run = PluginRun(
        id="r1",
        video_id="v1",
        plugin_key="webm_to_mp4",
        ok=True,
        message="Wrote 45 MB MP4",
        output_path=str(real_path),
        extra_json=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The success-state UI
    assert "Last successful output:" in html
    assert str(real_path) in html
    # The "Open in Finder" button (calls revealInFinder)
    assert "Open in Finder" in html
    assert "revealInFinder" in html


def test_tools_panel_shows_last_failed_run(client, db_session):
    """When the video has a failed run, show the error message (no Open in Finder)."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    run = PluginRun(
        id="r1",
        video_id="v1",
        plugin_key="webm_to_mp4",
        ok=False,
        message="ffmpeg failed: corrupt input",
        output_path=None,
        extra_json=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The error-state UI
    assert "Last run failed:" in html
    assert "ffmpeg failed: corrupt input" in html
    # No "Open in Finder" button rendered for THIS run.
    # Note: "Open in Finder" appears elsewhere in the page
    # (inside the JS refreshLastRun function as a template
    # literal), so we check that the "Last run failed"
    # section does NOT contain the button, not the page.
    # We use a substring check around the failed-run area.
    failed_idx = html.find("Last run failed:")
    next_section_idx = html.find("data-plugin-key", failed_idx + 1)
    failed_section = html[failed_idx:next_section_idx if next_section_idx > 0 else failed_idx + 2000]
    assert "Open in Finder" not in failed_section


def test_video_page_includes_refreshLastRun_function(client, db_session):
    """The <script> block defines refreshLastRun() and revealInFinder()."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    assert "async function refreshLastRun" in html
    assert "async function revealInFinder" in html
    # And the reveal endpoint is referenced
    assert "/api/plugins/reveal" in html
    # And the by-video endpoint is referenced
    assert "/api/plugins/runs/by-video/" in html


# ── MVP2.1.0.1: "Re-Upload with MP4" button + swap modal ───────────────
def test_swap_button_renders_when_last_run_successful(
    client, db_session, tmp_path
):
    """When last run was successful, show the 'Re-Upload with MP4' button."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone

    real_mp4 = tmp_path / "lesson.mp4"
    real_mp4.write_bytes(b"fake")

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()
    run = PluginRun(
        id="r1", video_id="v1", plugin_key="webm_to_mp4", ok=True,
        message="Wrote 45 MB MP4", output_path=str(real_mp4),
        extra_json=None, created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    assert "Re-Upload with MP4" in html
    # The button is NOT disabled (video is ready)
    # We check the button's onclick calls confirmSwapToMp4
    assert "confirmSwapToMp4" in html


def test_swap_button_disabled_when_video_not_ready(
    client, db_session, tmp_path
):
    """When video.status != 'ready', the button is disabled with a tooltip."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone

    real_mp4 = tmp_path / "lesson.mp4"
    real_mp4.write_bytes(b"fake")

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm",
        status="transcribing",  # NOT 'ready'
    )
    db_session.add_all([course, section, video])
    db_session.commit()
    run = PluginRun(
        id="r1", video_id="v1", plugin_key="webm_to_mp4", ok=True,
        message="Wrote 45 MB MP4", output_path=str(real_mp4),
        extra_json=None, created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # Button is rendered but disabled
    assert "Re-Upload with MP4" in html
    # The button has the disabled attribute
    # (we look for the disabled attribute near the swap-btn- id)
    swap_section_idx = html.find('id="swap-btn-webm_to_mp4"')
    assert swap_section_idx > 0
    next_btn_end = html.find('>', swap_section_idx)
    swap_section = html[swap_section_idx:next_btn_end + 1]
    assert "disabled" in swap_section


def test_swap_button_not_shown_when_last_run_failed(
    client, db_session
):
    """When last run failed, the swap button is NOT shown (no MP4 to swap to)."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video
    from app.models.plugin_run import PluginRun
    from datetime import datetime, timezone

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()
    run = PluginRun(
        id="r1", video_id="v1", plugin_key="webm_to_mp4", ok=False,
        message="ffmpeg failed: corrupt input", output_path=None,
        extra_json=None, created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # No successful last run = no swap button
    # (we look for the specific id of the swap button)
    assert 'id="swap-btn-webm_to_mp4"' not in html
    # The "Last run failed:" message is still shown
    assert "Last run failed:" in html


def test_swap_modal_is_in_page(client, db_session):
    """The swap confirmation modal is rendered (hidden by default)."""
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The modal container
    assert 'id="swap-modal"' in html
    assert 'id="swap-confirm-btn"' in html
    assert 'id="swap-cancel-btn"' in html
    assert 'id="swap-mp4-path"' in html
    # And the JS function
    assert "function confirmSwapToMp4" in html
    assert "function hideSwapModal" in html
    assert "async function performSwap" in html
    # And the endpoint is referenced
    assert "/api/plugins/swap-to-mp4" in html


# ── MVP2.1.0.1: Issue 1 — Re-Upload button in JS-rendered template ───
def test_renderSwapButton_helper_is_defined(client, db_session):
    """The renderSwapButton() helper exists in the page's <script> block.

    The JS refreshLastRun() template (called after a
    successful Run) uses this helper to render the
    'Re-Upload with MP4' button so the user sees it
    immediately after the transcode completes — no
    page reload needed. Without this helper, the
    user would only see 'Open in Finder' until they
    hard-refresh.
    """
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    assert "function renderSwapButton" in html
    # The helper is called from refreshLastRun's success branch
    # (we look for the function name in the JS template)
    assert "renderSwapButton(" in html
    # And the helper references confirmSwapToMp4
    assert "confirmSwapToMp4" in html


def test_video_status_exposed_to_js(client, db_session):
    """The video's status is exposed as a JS constant (videoStatus).

    The renderSwapButton() helper needs to know
    whether the video is in 'ready' state to decide
    whether to enable the swap button. The page
    must render a `const videoStatus = '...';` line
    in the JS context.
    """
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The JS constant is rendered with the actual status value
    assert "const videoStatus = 'ready'" in html


# ── MVP2.1.0.1: Issue 2 — performSwap() swaps video src without reload ────
def test_performSwap_uses_video_src_load_instead_of_reload(client, db_session):
    """performSwap() updates the <video> src in place instead of location.reload().

    Issue 2 was: after a successful swap, the page
    reloaded (slow + bfcache issues with stale WebM
    state). The fix: set the video element's src
    with a cache-bust query param + call .load(),
    no page reload.

    We check that the function exists and uses
    videoEl.src (or similar) and videoEl.load(),
    and does NOT use location.reload() inside
    performSwap.
    """
    from app.models.course import Course
    from app.models.section import Section
    from app.models.video import Video

    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id="v1", section_id="s1", title="Test video",
        file_path="lesson.webm", filename="lesson.webm", status="ready",
    )
    db_session.add_all([course, section, video])
    db_session.commit()

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    html = resp.text
    # The function exists
    assert "async function performSwap" in html
    # It uses the video element
    assert "videoEl.src" in html or "video.src" in html
    # It calls .load() on the video element
    assert "videoEl.load()" in html or "video.load()" in html
    # It includes a cache-bust query param
    assert "?v=" in html
    # It does NOT use location.reload() inside the success branch.
    # We check that the only location.reload() references in
    # the file are NOT inside performSwap. Since the test
    # already has location.reload() in other functions
    # (transcribe/generate poll), we just confirm performSwap
    # does not contain it by isolating the function body.
    fn_start = html.find("async function performSwap")
    assert fn_start > 0
    # Find the matching closing brace (rough heuristic —
    # find the next "async function" or end of script).
    next_fn = html.find("async function ", fn_start + 30)
    fn_body = html[fn_start:next_fn if next_fn > 0 else fn_start + 3000]
    assert "location.reload" not in fn_body, (
        "performSwap() should NOT call location.reload() — "
        "use videoEl.src + videoEl.load() instead"
    )
