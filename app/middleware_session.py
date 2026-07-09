"""Session-expiry redirect middleware (MVP2.0, item #7).

Problem (see doc/manualTodo.txt, item 3, 2026-07-09):
    When the user's `fb_token` session cookie expires (1 hour after
    login, per app/auth/session.py:COOKIE_MAX_AGE), the existing
    routes still render the page — but no auth-protected data
    (course list, video assets, generated materials) is available.
    The user sees a "phantom" page: the video player loads but
    every "Generate" / "Transcribe" / "Save" button silently 401s
    in the background.

Fix
---
    For protected server-side-rendered (SSR) routes, detect a
    *present-but-invalid* cookie and redirect to the dashboard
    with `?session=expired`. The dashboard then shows a non-blocking
    toast via `showToast()` (defined in app/templates/base.html)
    so the user knows why they were bounced.

Scope
-----
    Protected SSR routes (the ones that show user-specific data):

        /                       (dashboard)
        /course/{course_id}
        /video/{video_id}
        /chat-history

    Everything else is left alone:

        /api/*                  — return 401 JSON (clients handle it)
        /login                  — anonymous users need to land here
        /static/*               — public assets
        any other unknown path   — 404, not our problem

Why middleware, not per-route
-----------------------------
    The four protected routes are spread across three router
    modules (frontend, chat). A middleware catches all of them
    with one change and automatically covers any new protected
    SSR route added later (e.g. a future /profile or /settings
    page). Per-route would mean editing four handlers, easy to
    miss one.

Cookie "present-but-invalid" semantics
---------------------------------------
    This middleware ONLY redirects when:

        1. The request has an `fb_token` cookie, AND
        2. `verify_token` raises ValueError (token expired /
           malformed / revoked).

    A request with NO cookie (anonymous user) is NOT redirected.
    The existing templates already render a "Sign in" prompt for
    anonymous users (e.g. dashboard.html: the upload zone shows
    a "Sign in to start learning" card). Bouncing anonymous
    users to `/?session=expired` would be wrong — they never had
    a session in the first place.

    A request with a VALID cookie is also NOT redirected; the
    existing handlers do their normal work.

Performance
-----------
    The cookie is verified on every protected SSR request. That's
    one Firebase Admin SDK call per page load, which is the same
    cost the existing `get_current_user_optional` already pays
    inside each route handler. We could cache the result, but the
    MVP1 design explicitly avoids that (cached tokens go stale
    silently — that's the bug we're fixing). Verifying on every
    request is the correct trade-off for a single-user local app.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp

from app.auth.firebase_admin import verify_token
from app.auth.session import COOKIE_NAME


# Path prefixes that count as "protected SSR" routes. A request to any
# of these with a present-but-invalid session cookie gets redirected
# to `/?session=expired`. Order doesn't matter — these are all
# prefix-matched (see `_is_protected_ssr`).
_PROTECTED_SSR_PREFIXES: tuple[str, ...] = (
    "/course/",
    "/video/",
    "/chat-history",
)

# Path prefixes we never touch. Static assets, API, and the login page
# must keep their current behavior:
#   - /api/*  → 401 JSON, lets fetch() clients handle it
#   - /static → public assets, must be reachable even unauthenticated
#   - /login  → the only place anonymous users should land
#   - /api/auth/session → POST here is how users *get* their cookie,
#     redirecting would break login
_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/static",
    "/login",
    "/api/auth/session",
)


def _is_protected_ssr(path: str) -> bool:
    """Return True if `path` is a protected SSR route we should guard.

    The dashboard "/" is handled as a special case: it's protected
    only when a cookie is present-and-invalid. Anonymous visits to
    "/" stay on "/" (they see the "Sign in" prompt).
    """
    if path in _SKIP_PREFIXES:
        return False
    if any(path.startswith(p) for p in _PROTECTED_SSR_PREFIXES):
        return True
    return False


class SessionExpiryMiddleware(BaseHTTPMiddleware):
    """Redirect protected SSR requests with an expired cookie to /.

    On redirect, append `?session=expired` so the dashboard can show
    a one-time toast. The cookie itself is NOT cleared here — that's
    the user's explicit logout action (DELETE /api/auth/session).
    Leaving the cookie in place lets the client decide when to call
    DELETE; some users intentionally keep the cookie around for
    "remember me on this device" UX in a future iteration.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Only act on protected SSR routes. Everything else (API, static,
        # login, root-without-bad-cookie) passes through unchanged.
        # We treat "/" as protected ONLY when there's a cookie that
        # fails verification — see the special-case below.
        is_dashboard = path == "/"
        is_other_protected = _is_protected_ssr(path)

        if not (is_dashboard or is_other_protected):
            return await call_next(request)

        cookie_token = request.cookies.get(COOKIE_NAME)
        if not cookie_token:
            # Anonymous visit — leave alone, the page will render its
            # "Sign in" prompt.
            return await call_next(request)

        # Cookie is present. Verify it. If it's valid, let the request
        # through unchanged. If it fails, redirect to /?session=expired.
        try:
            verify_token(cookie_token)
        except ValueError:
            # Expired / invalid / revoked cookie. Redirect to dashboard
            # with a query param the dashboard's JS will surface as a toast.
            #
            # We use a 302 (temporary) rather than 303 (see-other) because
            # the user originally hit a GET URL; 302 with GET-method
            # preservation is the correct semantic for "you've been
            # temporarily sent to a different page".
            #
            # We do NOT delete the cookie in the response. Reasons:
            #   1. The user might want to refresh after signing in again
            #      from the dashboard and have the new session take over.
            #   2. Deleting on a redirect would race with the user's
            #      intended logout flow.
            #   3. The cookie will be overwritten by POST /api/auth/session
            #      on the next successful login.
            return RedirectResponse(
                url="/?session=expired",
                status_code=302,
            )

        # Cookie is valid — proceed normally.
        return await call_next(request)
