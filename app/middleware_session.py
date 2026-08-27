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

Cookie "absent" vs "present-but-invalid" semantics
--------------------------------------------------
Since MVP2.0.6 (2026-07-15) this middleware distinguishes three
cookie states for non-dashboard protected routes (/course/,
/video/, /chat-history):

    1. **Absent cookie** → redirect to /?session=expired. The
       user sees a phantom page otherwise (HTML shell renders,
       every API call 401s). Reported as manualTodo [jul14] #1
       "logout but still can see summary". The previous behavior
       was "leave alone, the page will render a Sign-in prompt"
       which only works for the dashboard, not for deep links
       like /video/{id}.
    2. **Present-but-invalid cookie** → redirect to
       /?session=expired. (Original behavior, unchanged.)
    3. **Valid cookie** → request proceeds. (Unchanged.)

For the dashboard `/`, the absent-cookie case is treated
differently: anonymous visits render the "Sign in" prompt
(unchanged), but a present-but-invalid cookie still redirects
to itself with ?session=expired (unchanged). Bouncing
anonymous dashboard visitors to /?session=expired would be
wrong (they never had a session).

Why "session=expired" for the absent-cookie case
------------------------------------------------
Using the same `?session=expired` query string for both
"absent" and "expired" cookies means the dashboard's existing
toast handler picks up both cases with no new code. From the
user's perspective, "your session is no longer valid" covers
both "you never had one" and "the one you had expired", so
the same toast is the right message. The only difference is
the toast variant text could be improved in a future UX pass
(e.g. "Sign in to continue" vs "Your session expired") — for
now they share the same toast.

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
        # login) passes through unchanged.
        #
        # MVP2.0.6 (2026-07-15): split the "protected" check into two:
        #   - `is_other_protected` (/course/, /video/, /chat-history):
        #     these ALWAYS require a valid cookie. Anonymous visits
        #     (no cookie) get bounced to /?session=expired because
        #     otherwise the user sees a "phantom" page where the HTML
        #     shell renders but every API call 401s. Reported as
        #     manualTodo [jul14] #1 — "logout but still can see summary".
        #   - `is_dashboard` (/): the dashboard is the "public landing
        #     page". Anonymous visits render the "Sign in" prompt; we
        #     don't bounce them. A present-but-invalid cookie DOES
        #     redirect (so a returning user with an expired cookie
        #     gets the toast instead of a silent empty dashboard).
        is_dashboard = path == "/"
        is_other_protected = _is_protected_ssr(path)

        if not (is_dashboard or is_other_protected):
            return await call_next(request)

        cookie_token = request.cookies.get(COOKIE_NAME)

        # MVP2.0.6: on a protected non-dashboard route, an absent
        # cookie means "you're anonymous" — and the page would
        # render a phantom shell with no data. Bounce to the
        # dashboard so the user gets the sign-in prompt (or, if
        # they had a session that just expired, the session-
        # expired toast). This is the same UX as a present-but-
        # invalid cookie, just with a different reason. We use the
        # same `?session=expired` query string so the dashboard's
        # existing toast handler picks it up.
        if not cookie_token and is_other_protected:
            return RedirectResponse(
                url="/?session=expired",
                status_code=302,
            )

        if not cookie_token:
            # Anonymous visit to the dashboard — leave alone, the
            # page will render its "Sign in" prompt.
            return await call_next(request)

        # Cookie is present. Verify it. If it's valid, let the request
        # through unchanged. If verification fails, decide whether to
        # bounce to /?session=expired or let the request through (so
        # downstream code can return 401 cleanly).
        #
        # Day 9 hotfix: only bounce for token-specific failures
        # (InvalidIdTokenError, ExpiredIdTokenError, RevokedIdTokenError,
        # ValueError). Network/availability errors (UNAVAILABLE, internal
        # Firebase errors) should NOT bounce — that was the bug where
        # phone users got "session expired" on transient network blips
        # while clicking a video. The previous `except Exception` was
        # too broad and caught network failures as if they were bad
        # cookies, redirecting users unnecessarily.
        #
        # Implementation: we import Firebase error types lazily (the
        # firebase_admin module is heavy and we don't want to block
        # startup). If they're not available, fall back to a tuple of
        # the parent + ValueError so the basic case still works.
        try:
            verify_token(cookie_token)
        except ValueError:
            # Documented contract: verify_token raises ValueError on
            # malformed/expired/revoked tokens.
            return RedirectResponse(
                url="/?session=expired",
                status_code=302,
            )
        except _token_error_classes() as exc:
            # Firebase-specific token errors (not network errors).
            # We check this AFTER ValueError because InvalidIdTokenError
            # is a subclass of FirebaseError which is not ValueError.
            if _is_token_quality_error(exc):
                return RedirectResponse(
                    url="/?session=expired",
                    status_code=302,
                )
            # Token quality unknown (likely a network/availability issue).
            # Let the request through — downstream code will return 401
            # on its own if needed, but the user won't see a phantom
            # "session expired" toast from a transient blip.
        except Exception:  # noqa: BLE001 — last-resort safety net
            # Unknown error type. Be conservative: let it through rather
            # than logging the user out. (Better to debug a 401 than to
            # bounce users on every unknown Firebase SDK quirk.)
            pass

        # Cookie is valid (or we couldn't verify but chose not to bounce) —
        # proceed normally.
        return await call_next(request)


def _token_error_classes() -> tuple[type, ...]:
    """Return Firebase token error classes (lazy import).

    Returns an empty tuple if firebase_admin isn't available — the
    ValueError catch above will still handle the common case.
    """
    try:
        from firebase_admin import exceptions as fb_exc
        return (fb_exc.FirebaseError,)
    except ImportError:
        return ()


def _is_token_quality_error(exc: BaseException) -> bool:
    """True if this FirebaseError means 'the token is bad', not 'network down'.

    Token quality errors → bounce to /?session=expired:
      - InvalidIdTokenError: token malformed or revoked
      - ExpiredIdTokenError: token past its expiry (we set 1h, so this
        means the user has been idle > 1h, or their clock drifted)
      - RevokedIdTokenError: user logged out elsewhere

    NOT token quality (let request through):
      - UnavailableError: Firebase backend unreachable
      - InternalError: something broke on Google's side
      - DeadlineExceededError: token verification timed out
    """
    try:
        from firebase_admin import exceptions as fb_exc
    except ImportError:
        return True  # conservative: treat unknown as token-quality
    return isinstance(exc, (fb_exc.InvalidIdTokenError,
                            fb_exc.ExpiredIdTokenError,
                            fb_exc.RevokedIdTokenError))
