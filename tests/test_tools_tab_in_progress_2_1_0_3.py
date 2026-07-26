"""Tests for the in-progress "Last run" visual state and auto-poll
on the Tools tab (MVP2.1.0.3).

Two regressions are covered:

  1. BUG (user-reported, post-MVP2.1.0.2):
     A plugin run in 'queued' or 'running' state rendered as
     "Last run failed: Running…" — because the old template only
     branched on ok=True/False. The user couldn't tell that the
     run was actually working.

  2. BUG (user-reported, post-MVP2.1.0.2):
     The forEach in switchTab() was missing 'tools' (the same
     regression that bit 'discuss' in MVP2.0.2). Clicking Tools
     then switching to another tab left the Tools panel visible
     underneath (multi-panel violation).

Fixes in MVP2.1.0.3:
  - Server-render now branches on `status in ('queued',
    'running')` FIRST and renders an indigo "⏳ Currently
    running…" / "Queued, waiting for a worker slot…" box.
  - JS refreshLastRun() mirrors the same 3-state template.
  - On page load, if any plugin's last run is in-progress,
    startAutoPollIfNeeded() begins a 1.5s poll loop that
    silently re-renders the box when the run reaches a
    terminal state — no manual reload required.
  - switchTab()'s forEach now includes 'tools'.

These tests cover the visible state of the box for all three
states, the auto-poll registration on page load, the
auto-poll's data-run-status update behavior, and the
multi-tab fix.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.plugin_run import PluginRun
from app.models.section import Section
from app.models.video import Video


# ── Helpers ─────────────────────────────────────────────────────────────
def _seed_video(db: Session, video_id: str = "v1") -> Video:
    """Create a Course -> Section -> Video chain for the tests."""
    course = Course(id="c1", user_id="u1", title="Test course")
    section = Section(id="s1", course_id="c1", title="Test section")
    video = Video(
        id=video_id,
        section_id="s1",
        title="Test video",
        file_path="lesson.webm",
        filename="lesson.webm",
        status="ready",
    )
    db.add_all([course, section, video])
    db.commit()
    db.refresh(video)
    return video


def _add_run(
    db: Session,
    *,
    run_id: str,
    status: str = "done",
    ok: bool = True,
    message: str = "Wrote 45 MB MP4",
    output_path: str | None = "/tmp/lesson.mp4",
) -> PluginRun:
    """Insert a PluginRun with the given status (queued / running /
    done / failed)."""
    run = PluginRun(
        id=run_id,
        video_id="v1",
        plugin_key="webm_to_mp4",
        status=status,
        ok=ok,
        message=message,
        output_path=output_path,
        extra_json=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ── Bug 1: 3-state visual rendering on server-render ───────────────────
def _extract_last_run_div(html: str, plugin_key: str = "webm_to_mp4") -> str:
    """Extract the *server-rendered* #last-run-{key} div from the
    page. We cut off at </body> so tests can assert on the visual
    state without being fooled by the inline <script> block
    (which contains all three states' text by design —
    refreshLastRun needs them to build the box client-side).

    Uses a div-balance counter (more reliable than a regex for
    nested Jinja-rendered divs of unknown depth).
    """
    # Cut off at </body> — everything after is JS source.
    # We must NOT use "<script" because the head contains a
    # tailwind <script src="..."> tag.
    body_end = html.find("</body>")
    if body_end > 0:
        html = html[:body_end]
    # Find the opening tag (may span multiple lines)
    open_pattern = re.compile(
        rf'<div\s+id="last-run-{re.escape(plugin_key)}"[^>]*>',
        re.DOTALL,
    )
    open_match = open_pattern.search(html)
    if not open_match:
        return ""
    start = open_match.end()
    # Walk the HTML counting <div and </div> (case-insensitive)
    # until the depth returns to 0. The opening tag itself
    # brought us to depth=1.
    depth = 1
    i = start
    div_open = re.compile(r'<div\b', re.IGNORECASE)
    div_close = re.compile(r'</div\s*>', re.IGNORECASE)
    while i < len(html) and depth > 0:
        next_open = div_open.search(html, i)
        next_close = div_close.search(html, i)
        if not next_close:
            return html[start:]  # malformed — return rest
        if next_open and next_open.start() < next_close.start():
            depth += 1
            i = next_open.end()
        else:
            depth -= 1
            i = next_close.end()
            if depth == 0:
                return html[open_match.start():i]
    return html[open_match.start():i] if depth == 0 else ""


def test_last_run_queued_renders_indigo_box(client: TestClient, db_session: Session):
    """When the last run is in 'queued' state, the server-render
    must show the indigo "Queued, waiting for a worker slot…"
    box — NOT the red "Last run failed" box.
    """
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r1", status="queued", ok=False,
        message="Queued, waiting for a free worker slot.",
    )

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    last_run = _extract_last_run_div(resp.text)

    # The indigo box
    assert "bg-indigo-50" in last_run, "In-progress runs should use indigo styling"
    assert "Queued, waiting for a worker slot" in last_run
    # The ⏳ icon
    assert "⏳" in last_run
    # The auto-poll hint
    assert "auto-refreshes" in last_run
    # The status must NOT render as failed
    assert "Last run failed" not in last_run, (
        "An in-progress run must NOT render the 'Last run failed' "
        "red box — that's the bug MVP2.1.0.3 fixes."
    )
    # data-run-status is set so the JS auto-poll can read it
    assert 'data-run-status="queued"' in last_run


def test_last_run_running_renders_indigo_box(client: TestClient, db_session: Session):
    """When the last run is in 'running' state, the server-render
    must show the indigo "Currently running…" box."""
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r1", status="running", ok=False,
        message="Encoding with ffmpeg (50% complete)",
    )

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    last_run = _extract_last_run_div(resp.text)

    assert "bg-indigo-50" in last_run
    assert "Currently running" in last_run
    assert "⏳" in last_run
    assert "Last run failed" not in last_run
    # data-run-status is set so the JS auto-poll can read it
    assert 'data-run-status="running"' in last_run


def test_last_run_done_ok_renders_green_box(client: TestClient, db_session: Session):
    """A terminal successful run must render the green box
    (not the new indigo box — the indigo box is for in-progress
    only)."""
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r1", status="done", ok=True,
        message="Wrote 45 MB MP4", output_path="/tmp/lesson.mp4",
    )

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    last_run = _extract_last_run_div(resp.text)

    assert "bg-green-50" in last_run
    assert "Last successful output:" in last_run
    # Not the indigo state
    assert "bg-indigo-50" not in last_run
    assert 'data-run-status="done"' in last_run


def test_last_run_done_failed_renders_red_box(client: TestClient, db_session: Session):
    """A terminal failed run (status='done', ok=False) must render
    the red box — NOT the new indigo box, NOT the green box."""
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r1", status="done", ok=False,
        message="ffmpeg failed: corrupt input", output_path=None,
    )

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    last_run = _extract_last_run_div(resp.text)

    assert "bg-red-50" in last_run
    assert "Last run failed:" in last_run
    assert "bg-indigo-50" not in last_run
    assert 'data-run-status="done"' in last_run


def test_last_run_explicit_failed_status_renders_red_box(
    client: TestClient, db_session: Session
):
    """A run with status='failed' (the new terminal-failed status
    in MVP2.1.0.1) must also render the red box, not the indigo
    in-progress box."""
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r1", status="failed", ok=False,
        message="ffmpeg not found on PATH", output_path=None,
    )

    resp = client.get("/video/v1")
    assert resp.status_code == 200
    last_run = _extract_last_run_div(resp.text)

    assert "bg-red-50" in last_run
    assert "Last run failed:" in last_run
    assert "bg-indigo-50" not in last_run


# ── Bug 1: JS auto-poll registration on page load ──────────────────────
def test_page_registers_auto_poll_on_load(client: TestClient, db_session: Session):
    """The video page must call startAutoPollIfNeeded() on page
    load (in the Init block at the bottom of the script). This
    is the JS hook that kicks off the silent 1.5s polling for
    in-progress runs.

    Note: the call lives in the Init block (not in a
    DOMContentLoaded listener) because that's where the rest of
    the page-init code lives (loadTranscript, etc.) and the
    scrubScriptForSandbox helper in the JSDOM tests strips the
    entire Init block — so the auto-poll doesn't run inside
    those sandbox tests, which is fine (the tests for the
    auto-poll behavior are in test_tools_tab_in_progress_2_1_0_3
    and the sandbox tests just verify the page loads).
    """
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text

    # The function must be defined
    assert "function startAutoPollIfNeeded" in html, (
        "startAutoPollIfNeeded() must be defined in the page"
    )
    # And must be invoked from the Init block (page-load time)
    assert "startAutoPollIfNeeded()" in html, (
        "startAutoPollIfNeeded() must be called on page load "
        "so the auto-poll kicks in"
    )
    # 1.5s interval is the contract — see the comment in the JS
    assert "POLL_INTERVAL_MS = 1500" in html


def test_refreshLastRun_updates_data_run_status(
    client: TestClient, db_session: Session
):
    """refreshLastRun() (called by the auto-poll) must update the
    data-run-status attribute on the container so the auto-poll
    loop can see when a run reaches a terminal state and stop
    polling. Without this update, the loop would either never
    stop or never start after the first refresh.
    """
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text

    # Look for the assignment inside the refreshLastRun body
    assert "container.dataset.runStatus" in html, (
        "refreshLastRun must write to container.dataset.runStatus "
        "so the auto-poll loop knows when the run is terminal"
    )
    # And to data-run-id (so future polls don't accidentally
    # re-poll a different run)
    assert "container.dataset.runId" in html


def test_refreshLastRun_has_three_state_template(
    client: TestClient, db_session: Session
):
    """The JS template in refreshLastRun() must branch on
    status in (queued, running) FIRST, then on ok+output_path,
    then the red fallback. This mirrors the server-render
    template so the box doesn't visually "jump" when the
    auto-poll fetches a new state.
    """
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text

    # Find the refreshLastRun function body
    m = re.search(
        r"async function refreshLastRun\s*\([^)]*\)\s*\{(.+?)\n\}",
        html,
        re.DOTALL,
    )
    assert m, "refreshLastRun function not found in video.html"
    body = m.group(1)

    # The 3 branches must all be present
    assert "run.status === 'queued'" in body, (
        "refreshLastRun must check for 'queued' status"
    )
    assert "run.status === 'running'" in body, (
        "refreshLastRun must check for 'running' status"
    )
    # The indigo styling for the in-progress state
    assert "bg-indigo-50" in body, (
        "refreshLastRun must render an indigo box for in-progress runs"
    )
    # The "Queued, waiting for a worker slot" label
    assert "Queued, waiting for a worker slot" in body
    # The "Currently running" label
    assert "Currently running" in body


# ── Bug 1: auto-poll sees data-run-status attribute ────────────────────
def test_in_progress_box_carries_run_id_and_status_for_poll(
    client: TestClient, db_session: Session
):
    """The indigo box must include data-run-id and data-run-status
    so startAutoPollIfNeeded() can find in-progress runs by
    scanning the DOM. Without these attributes the auto-poll
    would have nothing to poll against.
    """
    _seed_video(db_session)
    _add_run(
        db_session, run_id="r-xyz", status="running", ok=False,
        message="Encoding…",
    )

    resp = client.get("/video/v1")
    html = resp.text

    # The container div must have data-run-id and data-run-status
    assert 'data-run-id="r-xyz"' in html, (
        "In-progress box must carry data-run-id for the auto-poll"
    )
    assert 'data-run-status="running"' in html, (
        "In-progress box must carry data-run-status='running' for the auto-poll"
    )


# ── Bug 2: switchTab forEach covers all SIX tabs ───────────────────────
def test_switchTab_includes_tools_in_forEach(
    client: TestClient, db_session: Session
):
    """REGRESSION (MVP2.1.0.3): switchTab()'s forEach must include
    'tools'. Without it, opening Tools then switching to another
    tab left the Tools panel visible underneath (multi-panel
    violation). This is the same class of bug that hit 'discuss'
    in MVP2.0.2.
    """
    _seed_video(db_session)
    resp = client.get("/video/v1")
    html = resp.text

    # Extract switchTab body
    m = re.search(
        r"function\s+switchTab\s*\([^)]*\)\s*\{(.+?)\n\}",
        html,
        re.DOTALL,
    )
    assert m, "switchTab function not found in video.html"
    body = m.group(1)

    # Extract the forEach array literal
    for_each = re.search(
        r"\[\s*['\"]([a-z]+)['\"]([^]]*)\]\s*\.forEach",
        body,
    )
    assert for_each, "forEach call not found in switchTab"
    items = re.findall(
        r"['\"]([a-z]+)['\"]",
        for_each.group(1) + for_each.group(0),
    )
    assert "tools" in items, (
        f"switchTab's forEach is missing 'tools'. Found: {items}. "
        "This is the multi-panel regression that MVP2.1.0.3 fixes."
    )
    # And all the others too — for completeness
    for required in ("summary", "flashcards", "quiz", "mindmap", "discuss", "tools"):
        assert required in items, f"forEach missing {required!r}"
