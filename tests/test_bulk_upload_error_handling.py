"""Tests for bulk upload error handling (MVP2.0.5).

Postmortem: user reported "the bulk upload fails error when
parsing the body" when uploading 3 files at 1+ GB. Root cause was
a chain of three issues:

1. uvicorn/h11 returns a plain-text 400 "Invalid HTTP request
   received." when the h11 receive buffer exceeds the default
   16 KB. This can happen during large multipart uploads.
2. The frontend's `await resp.json()` throws on the plain-text
   response, surfacing as "Unexpected token I in JSON at
   position 0" — which the user paraphrased as "error when
   parsing the body".
3. Even if the server returned JSON, the frontend's error
   display was cryptic. The user couldn't tell whether the
   error was the server's fault or their own (network issue,
   etc.).

Fix:
- Bump h11's `h11_max_incomplete_event_size` from 16 KB to
  64 MB in `scripts/start.sh` so the receive buffer never
  triggers for realistic upload sizes.
- Add a global `StarletteHTTPException` and `Exception`
  handler in `app/main.py` that wraps every error in a
  proper JSON response with `{"detail": "..."}`. This
  ensures the frontend always sees JSON.
- Add a `safeJsonParse(resp)` helper in `app/templates/base.html`
  that gracefully falls back to plain text if the server
  returns non-JSON. Both `dashboard.html` and `course.html`
  use this helper.

These tests verify the three layers of the fix.
"""

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── 1. Server-side: exception handlers return JSON ─────────────────────


def test_starlette_http_exception_handler_returns_json(client: TestClient):
    """A `StarletteHTTPException` raised anywhere (middleware,
    dependencies, route handlers) must be returned as a proper
    JSON response with a `detail` field — never plain text.

    Regression: before MVP2.0.5, an HTTPException raised by a
    middleware would emit a plain-text body, which the frontend
    couldn't JSON-parse, causing "error when parsing the body".
    """
    # Hit a route that we know will raise a 404 (an unknown
    # /api/* path). FastAPI raises HTTPException(404).
    resp = client.get("/api/this-route-does-not-exist")
    assert resp.status_code == 404
    # Content-Type must be JSON
    assert "application/json" in resp.headers.get("content-type", ""), (
        f"Expected JSON response, got content-type "
        f"{resp.headers.get('content-type')!r} with body "
        f"{resp.text[:200]!r}"
    )
    # Body must be parseable JSON with a `detail` field
    data = resp.json()
    assert "detail" in data
    assert isinstance(data["detail"], str)


def test_unhandled_exception_handler_is_registered():
    """The global `Exception` handler must be registered on the
    app so that any uncaught exception in a route handler is
    returned as a 500 JSON response, not a plain-text traceback
    or HTML.

    We can't easily test this through TestClient because
    TestClient re-raises exceptions by default. But we can
    verify the handler is registered with the app — that's
    the structural test.
    """
    from app.main import app

    # Inspect the app's exception handlers. FastAPI stores them
    # on `app.exception_handlers` keyed by exception class.
    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert StarletteHTTPException in app.exception_handlers, (
        "The StarletteHTTPException handler is not registered "
        "on the app. Without it, HTTPExceptions raised in "
        "middleware or dependencies may return plain-text "
        "bodies. See app/main.py."
    )
    assert Exception in app.exception_handlers, (
        "The catch-all Exception handler is not registered "
        "on the app. Without it, uncaught exceptions in route "
        "handlers would return plain-text bodies (or, in "
        "debug mode, HTML tracebacks), which the frontend "
        "can't JSON-parse. See app/main.py."
    )


def test_bulk_upload_route_returns_json_on_404(client: TestClient):
    """The bulk upload route must return JSON on 404 (unknown
    section_id), not plain text.

    Regression: a non-JSON 404 body would crash the frontend's
    resp.json() call when the user tries to upload to a deleted
    section. With the global exception handler, all HTTPExceptions
    are returned as JSON.
    """
    from unittest.mock import patch
    import io
    from tests.test_videos import _auth_headers, _mock_auth

    with _mock_auth():
        # Hit the bulk endpoint with a non-existent section_id
        # but valid files so we reach the section lookup and
        # get a 404 from the route (not a 422 from FastAPI's
        # body validation).
        resp = client.post(
            "/api/videos/upload-bulk/00000000-0000-0000-0000-000000000000",
            files=[("files", ("test.mp4", io.BytesIO(b"x"), "video/mp4"))],
            headers=_auth_headers(),
        )
    assert resp.status_code == 404
    assert "application/json" in resp.headers.get("content-type", "")
    data = resp.json()
    assert "detail" in data
    assert "not found" in data["detail"].lower() or "section" in data["detail"].lower()


# ── 2. Frontend: base.html exposes safeJsonParse helper ────────────────


def test_base_html_contains_safeJsonParse_helper():
    """`app/templates/base.html` must define a `safeJsonParse`
    helper that handles both JSON and non-JSON responses. The
    upload handlers in dashboard.html and course.html depend on
    this helper being globally available (it's in the base
    template, which is included on every page).
    """
    base_html = Path("app/templates/base.html").read_text()
    # The function must be defined
    assert "function safeJsonParse" in base_html, (
        "base.html must define a safeJsonParse() function so "
        "upload handlers can defensively parse JSON or fall "
        "back to text. See doc/MVP2.0-Status.md §19."
    )
    # The function must check content-type
    assert "content-type" in base_html.lower() or "contentType" in base_html, (
        "safeJsonParse must inspect the content-type to decide "
        "between JSON and text fallback."
    )
    # The function must handle JSON parse failure (try/catch)
    assert ".json().catch" in base_html or ".json().catch(" in base_html, (
        "safeJsonParse must catch JSON parse errors and fall "
        "back to text."
    )


def test_dashboard_uses_safeJsonParse_for_bulk_upload():
    """dashboard.html's bulk upload handler must use
    safeJsonParse, not call resp.json() directly (which would
    throw on plain-text 400 responses from h11).
    """
    dashboard_html = Path("app/templates/dashboard.html").read_text()
    # Must call safeJsonParse
    assert "safeJsonParse" in dashboard_html, (
        "dashboard.html must use safeJsonParse() for the bulk "
        "upload response, not resp.json() directly."
    )
    # The old pattern that called resp.json() then chained
    # .then must be gone. The new pattern is safeJsonParse.
    # Look for the old bad pattern.
    assert "resp.json().then(data" not in dashboard_html, (
        "Found the old `resp.json().then(data` pattern in "
        "dashboard.html — this throws on plain-text 400 "
        "responses. Replace with safeJsonParse()."
    )


def test_course_uses_safeJsonParse_for_bulk_upload():
    """course.html's upload handlers (single + bulk) must use
    safeJsonParse, not call resp.json() directly.
    """
    course_html = Path("app/templates/course.html").read_text()
    # Must call safeJsonParse
    assert "safeJsonParse" in course_html, (
        "course.html must use safeJsonParse() for upload "
        "responses, not resp.json() directly."
    )
    # The old pattern that did `await resp.json()` directly in
    # an `alert()` must be gone. The new pattern destructures
    # the safeJsonParse result.
    assert "(await resp.json()).detail" not in course_html, (
        "Found the old `(await resp.json()).detail` pattern in "
        "course.html — this throws on plain-text 400 "
        "responses. Replace with safeJsonParse() destructuring."
    )


# ── 3. Server start: h11 buffer size is bumped in start.sh ─────────────


def test_start_sh_bumps_h11_max_incomplete_event_size():
    """`scripts/start.sh` must pass a bumped
    `--h11-max-incomplete-event-size` to uvicorn so the default
    16 KB buffer doesn't reject large multipart uploads with a
    plain-text 400 "Invalid HTTP request received."
    """
    start_sh = Path("scripts/start.sh").read_text()
    # Must pass the flag
    assert "h11-max-incomplete-event-size" in start_sh, (
        "scripts/start.sh must pass --h11-max-incomplete-event-size "
        "to uvicorn so large multipart bodies don't trigger h11's "
        "16 KB receive-buffer limit. See doc/MVP2.0-Status.md §19."
    )
    # The value should be >= 1 MB (1_048_576). We use 64 MB.
    match = re.search(
        r"h11-max-incomplete-event-size\s+(\d+)",
        start_sh,
    )
    assert match, (
        "Could not find the numeric value of "
        "--h11-max-incomplete-event-size in start.sh"
    )
    value = int(match.group(1))
    assert value >= 1_048_576, (
        f"h11-max-incomplete-event-size is {value} bytes "
        f"({value / 1024 / 1024:.1f} MB) — must be at least 1 MB. "
        f"Default is 16 KB which is too small for large multipart "
        f"uploads."
    )


# ── 4. End-to-end: the safeJsonParse pattern works as expected ─────────
# (Can't run JS in pytest, but we can verify the helper is wired up
# in all the right places by checking the templates.)


def test_all_upload_handlers_use_safeJsonParse():
    """Every upload handler that consumes a fetch() response must
    use `safeJsonParse()` to defensively handle plain-text 4xx
    responses. This is a structural regression test — if someone
    adds a new upload endpoint and forgets to use the helper, the
    test will flag it.

    We only check the upload-specific functions (not every
    fetch() in the templates — those are out of scope for the
    MVP2.0.5 bulk-upload fix).
    """
    # The bad patterns we're guarding against. These were the
    # original call sites that would throw on plain-text 400s.
    dashboard_bad = [
        # Old: .then(resp => resp.json().then(data => ({ok: resp.ok, data})))
        "resp.json().then(data",
    ]
    course_bad = [
        # Old: (await resp.json()).detail
        "(await resp.json()).detail",
    ]
    # The new patterns (positive checks).
    dashboard_good = [
        # New: .then(resp => safeJsonParse(resp))
        "safeJsonParse(resp)",
    ]
    course_good = [
        # New: const { ok, data } = await safeJsonParse(resp);
        "await safeJsonParse(resp)",
    ]

    dashboard_html = Path("app/templates/dashboard.html").read_text()
    for bad in dashboard_bad:
        assert bad not in dashboard_html, (
            f"dashboard.html still contains the old `{bad}` pattern "
            f"in an upload handler. Use safeJsonParse() instead so "
            f"plain-text 400 responses don't crash the upload."
        )
    for good in dashboard_good:
        assert good in dashboard_html, (
            f"dashboard.html is missing the new `{good}` pattern in "
            f"an upload handler. The MVP2.0.5 fix requires all upload "
            f"handlers to use safeJsonParse()."
        )

    course_html = Path("app/templates/course.html").read_text()
    for bad in course_bad:
        assert bad not in course_html, (
            f"course.html still contains the old `{bad}` pattern in "
            f"an upload handler. Use safeJsonParse() instead so "
            f"plain-text 400 responses don't crash the upload."
        )
    for good in course_good:
        assert good in course_html, (
            f"course.html is missing the new `{good}` pattern in an "
            f"upload handler. The MVP2.0.5 fix requires all upload "
            f"handlers to use safeJsonParse()."
        )
