"""Template-source regression tests for the 2026-09-22 upload rewrite.

What happened: two real multi-file picks (18 files ~18GB, then 8
files) "failed with some error" while the server log stayed clean —
the bundled bulk POST died at Cloudflare's edge (100MB per-REQUEST
cap on the free plan) before reaching the server. The rewrite sends
each file in its own tunnel-safe request.

These tests read the template SOURCE (the house pattern — see
tests/test_bulk_upload_error_handling.py). No JS runs in pytest;
the strings pinned here are the load-bearing wiring:

  1. The multi-file path is per-file sequential (no bundled
     /upload-bulk POST from either page).
  2. Both big-file (>100MB) and small-file branches exist in the
     multi-file loop, so every file takes a tunnel-safe path.
  3. A 429 stops the batch (cap reasons apply to the whole batch).
  4. Every client-side failure is beamed via
     Telemetry.trackUploadError — the "no server-side trace"
     observability fix.
  5. The banner dismissal key round-trips: loadInterruptedBanner
     stashes it, dismissInterrupted reads the SAME key (the
     original bug: the dismiss side re-derived a DIFFERENT key from
     the display text, so dismissals never stuck).
"""

import re
from pathlib import Path

COURSE = Path("app/templates/course.html").read_text()
DASHBOARD = Path("app/templates/dashboard.html").read_text()
TELEMETRY_JS = Path("app/static/js/telemetry.js").read_text()


# ── 1. The bundled bulk POST is gone from both pages ─────────────────────

def test_course_multi_upload_is_per_file_not_bundled():
    """course.html must NOT bundle multiple files into one POST —
    that request dies at Cloudflare's 100MB edge cap with zero
    server-side trace (the 2026-09-22 incidents)."""
    assert "upload-bulk/" not in COURSE, (
        "course.html still bundles files into one /upload-bulk POST. "
        "Multi-file picks must upload per-file (each request fits "
        "the tunnel's 100MB edge cap)."
    )


def test_dashboard_multi_upload_is_per_file_not_bundled():
    assert "upload-bulk/" not in DASHBOARD, (
        "dashboard.html still bundles files into one /upload-bulk "
        "POST. Multi-file picks must upload per-file."
    )


# ── 2. Both size branches exist inside the multi-file loop ────────────────

def test_course_multi_loop_has_chunked_and_single_branches():
    """Inside the per-file loop: >100MB → chunked session, ≤100MB →
    single endpoint. Both branches must exist or some file sizes
    have no safe path."""
    # The loop must branch on the threshold inside the multi-file path.
    assert "f.size > CHUNK_THRESHOLD" in COURSE, (
        "course.html's multi-file loop must branch on "
        "CHUNK_THRESHOLD so big files take the chunked path."
    )
    # The chunked-session uploader must be reachable from the loop
    # (it's also used by the single-big-file path).
    assert "uploadChunked(sectionId, f, {" in COURSE, (
        "course.html's multi-file loop must call uploadChunked for "
        "files over the threshold."
    )


def test_dashboard_multi_loop_has_chunked_and_single_branches():
    assert "CHUNK_THRESHOLD = 100 * 1024 * 1024" in DASHBOARD, (
        "dashboard.html must define the edge-cap threshold."
    )
    assert "f.size > CHUNK_THRESHOLD" in DASHBOARD, (
        "dashboard.html's multi-file loop must branch on "
        "CHUNK_THRESHOLD."
    )
    assert "/api/upload-sessions/init" in DASHBOARD, (
        "dashboard.html's big-file branch must use the chunked "
        "session API."
    )


# ── 3. A 429 stops the batch with the honest reason ───────────────────────

def test_course_multi_loop_stops_on_429():
    """Cap rejections (429) apply to the whole batch — the loop must
    stop instead of hammering the endpoint with files it already
    knows will be refused."""
    assert "if (resp.status === 429) { aborted = reason; break; }" in COURSE


def test_dashboard_multi_loop_stops_on_429():
    assert "if (resp.status === 429) { capStopped = reason; break; }" in DASHBOARD


# ── 4. Client-side failures are beamed to the server ─────────────────────

def test_course_reports_client_side_upload_failures():
    """Every client-side failure branch must call reportUploadError
    (the ui.upload beacon) — otherwise edge/network failures leave
    no server-side trace (the 'why no logs?!' question)."""
    assert "function reportUploadError" in COURSE, (
        "course.html must define reportUploadError (the ui.upload "
        "failure beacon wrapper)."
    )
    assert "Telemetry.trackUploadError" in COURSE, (
        "course.html's beacon wrapper must go through "
        "window.Telemetry.trackUploadError."
    )
    # The three failure branches: small-single, multi-file per-file,
    # and the outer catch (chunked/big-file path).
    assert COURSE.count("reportUploadError(") >= 3, (
        "course.html must beam from all client-side failure branches "
        f"(found {COURSE.count('reportUploadError(')} call sites, "
        "need ≥ 3)."
    )


def test_dashboard_reports_client_side_upload_failures():
    assert "trackUploadError" in DASHBOARD, (
        "dashboard.html's failure branches must beam via "
        "Telemetry.trackUploadError."
    )


def test_telemetry_js_exports_track_upload_error():
    """The beacon must expose trackUploadError for the pages."""
    assert "trackUploadError: trackUploadError," in TELEMETRY_JS, (
        "telemetry.js must export trackUploadError."
    )
    # And route it to the ui.upload source the server allowlists.
    assert "track('ui.upload'" in TELEMETRY_JS, (
        "trackUploadError must emit source 'ui.upload' — the "
        "allowlisted name in app/routers/telemetry.py."
    )


# ── 5. The banner dismissal key round-trips ──────────────────────────────

def test_banner_dismissal_key_load_and_dismiss_agree():
    """THE original bug: loadInterruptedBanner keys localStorage on
    filename:declared_size, but dismissInterrupted re-derived its
    key from the banner's DISPLAY text — never a match, so the
    banner nagged on every course-page load.

    The fix: the load side stashes the key on the element's
    dataset (el.dataset.dismissKey = key) and the dismiss side
    reads THAT (el.dataset.dismissKey). This test pins both halves.
    """
    # Load side stashes.
    assert "el.dataset.dismissKey = key" in COURSE, (
        "loadInterruptedBanner must stash the dismissal key on the "
        "banner element (el.dataset.dismissKey = key)."
    )
    # Dismiss side uses the stashed key — NOT display text.
    assert "localStorage.setItem(el.dataset.dismissKey, '1')" in COURSE, (
        "dismissInterrupted must persist the dismissal with the SAME "
        "key the load side stashed (el.dataset.dismissKey)."
    )
    # The old buggy pattern must be gone: keying on detail.textContent.
    assert "localStorage.setItem(\n" not in COURSE.split("function dismissInterrupted")[1].split("\n}")[0] or True
    dismiss_fn = COURSE.split("function dismissInterrupted")[1].split("\n}")[0]
    assert "detail.textContent" not in dismiss_fn, (
        "dismissInterrupted still derives its key from the display "
        "text (detail.textContent) — the exact bug that made "
        "dismissals never stick."
    )


# ── 6. The load-side key construction is unchanged (sanity) ──────────────

def test_banner_load_side_still_builds_the_canonical_key():
    """The canonical key format ('uploadBannerDismissed:' +
    filename + ':' + declared_size) is what BOTH sides must agree
    on. Pin the load side's construction so nobody silently
    changes one half."""
    assert (
        "const key = 'uploadBannerDismissed:' + data.filename + ':' + data.declared_size;"
        in COURSE
    ), (
        "loadInterruptedBanner's canonical dismissal key must stay "
        "'uploadBannerDismissed:<filename>:<declared_size>'."
    )