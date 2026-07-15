"""Tests for SessionExpiryMiddleware (MVP2.0, item #7).

Covers all branches of the redirect logic plus the dashboard's
?session=expired toast trigger:

  - No cookie → no redirect (anonymous visit is normal)
  - Valid cookie → no redirect (request proceeds)
  - Invalid/expired cookie on protected path → 302 to /?session=expired
  - Invalid/expired cookie on /api/* → no redirect (keep 401 JSON)
  - Invalid/expired cookie on /login → no redirect (login is the
    destination for expired sessions, not a place to bounce)
  - Invalid/expired cookie on /static/* → no redirect
  - Invalid/expired cookie on /api/auth/session → no redirect
    (POSTing here is how users GET a cookie)
  - The redirected response carries the security headers too
  - The dashboard's ?session=expired trigger JS is present in
    base.html and runs on the redirect target
  - The showToast() definition moved from video.html to base.html
    (one source of truth, available on every page)
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth.session import COOKIE_NAME


FAKE_USER = {"uid": "test-uid", "email": "t@example.com"}


# ── Pure helper coverage ─────────────────────────────────────────────────────


class TestIsProtectedSsr:
    """The _is_protected_ssr helper decides which paths get guarded.

    The helper returns False for "/" on purpose — the dashboard gets
    a special-case in the middleware itself (only redirect when a
    bad cookie is present). This separation is what lets an
    anonymous visit to "/" render the Sign-in prompt.
    """

    def test_course_path_is_protected(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/course/abc-123") is True

    def test_video_path_is_protected(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/video/abc-123") is True

    def test_chat_history_is_protected(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/chat-history") is True

    def test_dashboard_is_NOT_protected_by_helper(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/") is False

    def test_api_paths_are_skipped(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/api/courses") is False
        assert _is_protected_ssr("/api/auth/me") is False
        assert _is_protected_ssr("/api/videos/abc/transcribe") is False

    def test_login_is_skipped(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/login") is False

    def test_static_is_skipped(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/static/css/main.css") is False
        assert _is_protected_ssr("/static/js/foo.js") is False

    def test_api_auth_session_is_skipped(self):
        # POST /api/auth/session is how users GET a cookie; redirecting
        # would break the login flow itself.
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/api/auth/session") is False

    def test_unknown_path_is_not_protected(self):
        from app.middleware_session import _is_protected_ssr
        assert _is_protected_ssr("/totally-unknown") is False
        assert _is_protected_ssr("/settings/profile") is False


# ── Mock helpers ─────────────────────────────────────────────────────────────
#
# The middleware imports verify_token at module load time:
#     from app.auth.firebase_admin import verify_token
# Same for app.auth.dependencies. Patching the source
# (app.auth.firebase_admin.verify_token) does NOT replace the
# already-bound references in those modules. We patch ALL THREE
# bindings (source, middleware, dep) to a single MagicMock so every
# call site sees the same answer regardless of which module invokes
# verify_token first.
#
# The real firebase_admin SDK's exceptions are NOT ValueError
# subclasses (they inherit from FirebaseError), so the real SDK can't
# be exercised in tests using a fake JWT string. The mocks below
# raise ValueError directly, matching the contract documented in
# app/auth/firebase_admin.py:verify_token's docstring.


def _middleware_sees_expired():
    """Patch every verify_token binding to raise ValueError.

    Returns a context manager. The middleware sees the bad token,
    the dep (if exercised) sees the same bad token, the response is
    consistent.
    """
    from unittest.mock import MagicMock
    mock = MagicMock(side_effect=ValueError("expired"))
    return _MultiPatch([
        patch("app.auth.firebase_admin.verify_token", mock),
        patch("app.middleware_session.verify_token", mock),
        patch("app.auth.dependencies.verify_token", mock),
        patch("app.auth.session.verify_token", mock),
    ])


def _middleware_sees_valid(user=None):
    """Patch every verify_token binding to return a valid user."""
    from unittest.mock import MagicMock
    mock = MagicMock(return_value=user or FAKE_USER)
    return _MultiPatch([
        patch("app.auth.firebase_admin.verify_token", mock),
        patch("app.middleware_session.verify_token", mock),
        patch("app.auth.dependencies.verify_token", mock),
        patch("app.auth.session.verify_token", mock),
    ])


class _MultiPatch:
    """Tiny helper: `with` block that activates a list of patch() objects.

    contextlib.ExitStack would also work; rolling our own keeps the
    dependency surface minimal.
    """

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


# ── End-to-end behavior via TestClient ───────────────────────────────────────


def test_protected_video_route_with_expired_cookie_redirects(client: TestClient):
    """GET /video/{id} with a bad cookie should 302 to /?session=expired."""
    with _middleware_sees_expired():
        response = client.get(
            "/video/00000000-0000-0000-0000-000000000000",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_protected_course_route_with_expired_cookie_redirects(client: TestClient):
    """GET /course/{id} with a bad cookie should also redirect."""
    with _middleware_sees_expired():
        response = client.get(
            "/course/00000000-0000-0000-0000-000000000000",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_protected_chat_history_with_expired_cookie_redirects(client: TestClient):
    """/chat-history with a bad cookie redirects to dashboard."""
    with _middleware_sees_expired():
        response = client.get(
            "/chat-history",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_dashboard_with_expired_cookie_redirects(client: TestClient):
    """GET / with a bad cookie also redirects to itself with ?session=expired.

    This is the special-case behavior: a logged-in user returning to
    the dashboard with an expired cookie gets the toast instead of a
    silent "no courses" dashboard.
    """
    with _middleware_sees_expired():
        response = client.get(
            "/",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_protected_route_with_valid_cookie_is_not_redirected(client: TestClient):
    """A valid cookie should pass through to the normal handler.

    We use a UUID that's guaranteed not to exist, so the route
    itself returns 404 — but the important thing is that the
    middleware didn't intercept the request with a 302.
    """
    with _middleware_sees_valid():
        response = client.get(
            "/video/00000000-0000-0000-0000-000000000000",
            cookies={COOKIE_NAME: "valid-token"},
            follow_redirects=False,
        )
    assert response.status_code == 404
    assert "location" not in {k.lower() for k in response.headers.keys()}


def test_protected_video_route_with_no_cookie_redirects(client: TestClient):
    """MVP2.0.6: anonymous visit to /video/{id} (no cookie) now
    redirects to /?session=expired, instead of rendering a
    phantom page where the HTML shell loads but every API call
    401s. Reported as manualTodo [jul14] #1 "logout but still
    can see summary". The previous behavior (404, no redirect)
    was a UX bug — the user saw an empty page with no
    explanation. Now they get the same toast as the
    present-but-invalid case.
    """
    # MVP2.0.6: the conftest client fixture sets a default valid
    # cookie. Clear it so we can test the no-cookie path.
    client.cookies.clear()
    response = client.get(
        "/video/00000000-0000-0000-0000-000000000000",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_protected_course_route_with_no_cookie_redirects(client: TestClient):
    """MVP2.0.6: anonymous visit to /course/{id} (no cookie)
    also redirects. Same rationale as the video test above.
    """
    client.cookies.clear()
    response = client.get(
        "/course/00000000-0000-0000-0000-000000000000",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_protected_chat_history_route_with_no_cookie_redirects(client: TestClient):
    """MVP2.0.6: anonymous visit to /chat-history (no cookie)
    also redirects. Same rationale as the video test above.
    """
    client.cookies.clear()
    response = client.get(
        "/chat-history",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/?session=expired"


def test_dashboard_with_no_cookie_is_not_redirected(client: TestClient):
    """Anonymous visit to the dashboard is still allowed (no
    redirect) so the user sees the "Sign in" prompt. This is
    the special case — the dashboard is the public landing
    page, and bouncing anonymous visitors to /?session=expired
    would be wrong (they never had a session). The
    MVP2.0.6 fix only changes behavior for the OTHER
    protected routes (/course/, /video/, /chat-history), NOT
    the dashboard.
    """
    response = client.get(
        "/",
        follow_redirects=False,
    )
    # 200 (dashboard renders) — not a redirect.
    assert response.status_code == 200
    assert "location" not in {k.lower() for k in response.headers.keys()}


def test_api_route_with_expired_cookie_is_not_redirected(client: TestClient):
    """API routes keep their existing 401 behavior — no HTML redirect.

    The middleware must NEVER redirect API requests, even when the
    cookie is bad. The route's own auth dependency will return 401
    JSON and the client (fetch / curl) handles it.

    We patch verify_token so the real Firebase Admin SDK doesn't
    run on a fake JWT (the SDK raises InvalidIdTokenError, not
    ValueError, and that's a separate concern from #7).
    """
    with _middleware_sees_expired():
        response = client.get(
            "/api/courses",
            cookies={COOKIE_NAME: "this-is-not-a-real-jwt"},
            follow_redirects=False,
        )
    # 401 from the route's dep, NOT 302 from the middleware.
    assert response.status_code == 401
    # 401 responses are JSON, not HTML.
    assert "text/html" not in response.headers.get("content-type", "")


def test_login_route_with_expired_cookie_is_not_redirected(client: TestClient):
    """/login must always be reachable, even with a bad cookie.

    This is critical: a user whose session just expired needs to be
    able to LAND on /login to re-authenticate. Bouncing them would
    create a redirect loop.
    """
    with _middleware_sees_expired():
        response = client.get(
            "/login",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_static_route_with_expired_cookie_is_not_redirected(client: TestClient):
    """/static/* assets are public; bad cookie must not block them."""
    with _middleware_sees_expired():
        response = client.get(
            "/static/css/transcript-follow.css",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    # Either 200 (file found) or 404 (file missing) — both prove the
    # middleware didn't redirect.
    assert response.status_code in (200, 404)
    assert response.status_code != 302


def test_api_auth_session_with_expired_cookie_is_not_redirected(client: TestClient):
    """POST /api/auth/session is how users GET a cookie; never redirect.

    The middleware explicitly skips this path so a user whose cookie
    expired can still POST a fresh ID token and get a new cookie.

    We patch verify_token so the real Firebase Admin SDK doesn't
    run on a fake JWT (the SDK raises InvalidIdTokenError, not
    ValueError, and that's a separate concern from #7).
    """
    with _middleware_sees_expired():
        response = client.post(
            "/api/auth/session",
            json={"id_token": "fake-id-token"},
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    # 401 from the route's own verify_token call — proves we reached
    # the route and weren't redirected.
    assert response.status_code == 401


def test_redirect_response_carries_security_headers(client: TestClient):
    """The 302 redirect must still have the baseline security headers.

    Without this, an attacker could chain the redirect into an XSS
    via missing CSP. The middleware order in app/main.py puts
    SecurityHeadersMiddleware OUTSIDE SessionExpiryMiddleware, so
    its dispatch wraps the redirect response too.
    """
    with _middleware_sees_expired():
        response = client.get(
            "/video/00000000-0000-0000-0000-000000000000",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    # A representative subset of the baseline headers from
    # SecurityHeadersMiddleware. (Full list in test_security_headers.py.)
    assert "content-security-policy" in response.headers, (
        f"Missing CSP on 302. Headers: {dict(response.headers)}"
    )
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_cookie_is_not_cleared_on_redirect(client: TestClient):
    """The redirect must NOT delete the cookie in its response.

    Reasons documented in app/middleware_session.py:
    1. User can refresh after signing in and have the new session
       take over.
    2. Deleting on a redirect would race with the intended logout.
    3. POST /api/auth/session will overwrite the cookie on next login.
    """
    with _middleware_sees_expired():
        response = client.get(
            "/video/00000000-0000-0000-0000-000000000000",
            cookies={COOKIE_NAME: "expired-or-malformed"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    # No Set-Cookie header should clear the cookie.
    set_cookie = response.headers.get("set-cookie", "")
    assert "fb_token=" not in set_cookie or "Max-Age=0" not in set_cookie


# ── Toast trigger on the dashboard ──────────────────────────────────────────


def test_dashboard_includes_session_expired_trigger_script(client: TestClient):
    """base.html must include the IIFE that detects ?session=expired.

    The middleware redirects to /?session=expired, so the dashboard
    HTML must contain the JS that shows the toast and strips the
    query param. This is the user-visible half of #7.
    """
    response = client.get("/")
    assert response.status_code == 200
    # The plain dashboard shouldn't have the toast in the URL, but
    # the script that LOOKS for it must be present.
    assert "session') === 'expired'" in response.text
    # The toast text (always in the script bundle, regardless of URL)
    assert "Your session has expired" in response.text


def test_dashboard_defines_show_toast_globally(client: TestClient):
    """showToast() must be defined in base.html so the toast works
    on the dashboard (and every other page that uses base.html)."""
    response = client.get("/")
    assert response.status_code == 200
    # The function definition is in base.html
    assert "function showToast(message, type = 'info')" in response.text
    # And it's exposed on window so pages can call it after the
    # initial script has run
    assert "window.showToast = showToast" in response.text


def test_video_page_no_longer_defines_show_toast_locally(client: TestClient):
    """showToast() definition must have been REMOVED from video.html.

    The function now lives in base.html. If both define it, the
    last-loaded wins and the base.html version is shadowed on
    video.html. We count: there should be exactly ONE definition
    in the rendered video page (the one from base.html).
    """
    # Build a course/section/video so the page renders, using a
    # valid mock for the route's auth dependency.
    with patch("app.auth.dependencies.verify_token", return_value=FAKE_USER):
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers={"Authorization": "Bearer x"}
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "W1"}, headers={"Authorization": "Bearer x"}
        )
        section_id = section_resp.json()["section_id"]
        import io
        upload = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("l.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers={"Authorization": "Bearer x"},
        )
        video_id = upload.json()["video_id"]
        response = client.get(f"/video/{video_id}")
    assert response.status_code == 200
    # Count: base.html has 1 definition. If video.html also defines
    # it locally we'd see 2. Allow exactly 1.
    assert response.text.count("function showToast(") == 1, (
        "video.html should reference (not redefine) showToast. "
        "The function now lives in base.html."
    )


# ── The redirect target itself renders the toast ────────────────────────────


def test_dashboard_rendered_via_session_expired_query_param_works(client: TestClient):
    """Hitting /?session=expired directly (not via redirect) renders
    the dashboard with the trigger JS in place. This proves the
    query param is handled by the same code path whether the user
    came from a redirect or typed the URL by hand."""
    response = client.get("/?session=expired")
    assert response.status_code == 200
    assert "Your session has expired" in response.text


def test_session_expired_query_param_does_not_break_dashboard_for_anon(client: TestClient):
    """Anonymous visit to /?session=expired should still render the
    dashboard (and show the toast). It should NOT redirect again
    (that would loop)."""
    response = client.get("/?session=expired", follow_redirects=False)
    assert response.status_code == 200
    assert "Your session has expired" in response.text
