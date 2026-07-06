"""Security headers middleware.

Adds baseline hardening headers to every HTTP response, in line with
OWASP recommendations and the [SECURITY.md] policy doc. Cheap, no
runtime cost, no dependencies — runs as pure ASGI middleware so it
covers the API, the Jinja2 template routes, the /api/auth/session
endpoint, and the static file serving uniformly.

Headers added
-------------

- `Content-Security-Policy` — defense-in-depth against XSS. Allows
  only the resources the app actually uses (CDN scripts, inline JS
  for the mindmap bootstrap, Firebase auth images, etc.). Block
  everything else by default. If a future feature needs a new
  origin, add it here and document why.
- `X-Frame-Options: DENY` — prevent clickjacking. The app has no
  legitimate reason to be embedded in an <iframe>.
- `X-Content-Type-Options: nosniff` — prevent MIME-type sniffing.
  Stops the browser from "helpfully" re-interpreting a file as
  something it's not.
- `Referrer-Policy: no-referrer` — don't leak our URLs to third
  parties (e.g. when a user clicks a link to an external resource).
- `Permissions-Policy` — disable browser features we don't use
  (camera, microphone, geolocation, payment, USB, etc.). Limits
  the blast radius if an attacker gets JS execution via XSS.
- `Strict-Transport-Security` — only set when behind HTTPS (in
  production). Tells the browser to upgrade future requests to
  HTTPS for one year.
- `Cross-Origin-Opener-Policy: same-origin-allow-popups` — isolate the browsing
  context. Stops cross-window attacks.
- `Cross-Origin-Embedder-Policy: credentialless` — require explicit
  opt-in from embedded resources, BUT allow cross-origin resources
  that don't send `Cross-Origin-Resource-Policy` as long as they
  don't carry credentials (cookies, HTTP auth, client certs).
  See the note below for why we use `credentialless` instead of the
  stricter `require-corp`.

Headers NOT set
---------------

- `X-XSS-Protection` — deprecated; modern browsers ignore it and
  CSP is the recommended replacement.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


# ── Content Security Policy ──────────────────────────────────────────────────
# The app uses:
#   - inline <script> blocks in the Jinja2 templates (small bootstrap
#     snippets, the Markmap preloader, the drag/pan handlers, etc.)
#   - External scripts from cdn.jsdelivr.net (Markmap), unpkg.com
#     (htmx), cdn.tailwindcss.com (Tailwind), and yuanfengli168.github.io
#     (AuthKit)
#   - Firebase auth which uses gstatic.com for images
#   - Data: URIs in the markdown-to-HTML converter for inline images
#   - Blob: URIs in the markmap export flow
#
# If you add a new external service, append its origin here AND
# add a comment explaining what it's for.
#
# Note on 'unsafe-eval': required because Markmap uses d3 internals
# that call `eval()` / `new Function()` to render the mindmap. We
# tested the alternative (removing it) and the mindmap silently
# fails to render. The risk is small — `unsafe-eval` only affects
# the app's own origin (no cross-origin eval) — and is the
# standard trade-off for any app using d3 / observablehq-style
# libraries.
#
# Note on wasm-unsafe-eval: an earlier version of this policy
# included `wasm-unsafe-eval` for Markmap. That directive is
# CSP Level 3 (still a draft) and Chrome/Firefox log an
# "Unrecognized Content-Security-Policy directive" warning on
# every page load. Markmap doesn't actually use WASM (it uses
# plain JS + d3), so the directive was a no-op that just created
# console noise. Removed 2026-07-06.
CSP = (
    "default-src 'self'; "
    # AuthKit's bundled modules dynamically import the Firebase SDK
    # from gstatic.com (firebase-app.js, firebase-auth.js, etc.),
    # so we must whitelist gstatic.com here as well as the four
    # CDN hosts we use directly.
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "https://cdn.jsdelivr.net https://unpkg.com "
    "https://cdn.tailwindcss.com https://yuanfengli168.github.io "
    "https://www.gstatic.com https://apis.google.com; "
    "style-src 'self' 'unsafe-inline' "
    "https://cdn.jsdelivr.net https://cdn.tailwindcss.com "
    "https://yuanfengli168.github.io; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "connect-src 'self' "
    "https://cdn.jsdelivr.net https://yuanfengli168.github.io "
    "https://firestore.googleapis.com https://identitytoolkit.googleapis.com "
    "https://www.googleapis.com "
    "https://apis.google.com https://accounts.google.com; "
    "frame-src 'self' https://yuanfengli168.github.io "
    "https://accounts.google.com https://*.firebaseapp.com; "
    "worker-src 'self' blob:; "
    "child-src 'self' https://yuanfengli168.github.io "
    "https://accounts.google.com https://*.firebaseapp.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "upgrade-insecure-requests"
)

# Permissions-Policy disables browser features we don't use. If a
# future feature needs one (e.g. the camera for video recording),
# remove that feature from the list.
PERMISSIONS_POLICY = (
    "accelerometer=(), "
    "autoplay=(), "
    "camera=(), "
    "cross-origin-isolated=(), "
    "display-capture=(), "
    "encrypted-media=(), "
    "fullscreen=(self), "
    "geolocation=(), "
    "gyroscope=(), "
    "keyboard-map=(), "
    "magnetometer=(), "
    "microphone=(), "
    "midi=(), "
    "payment=(), "
    "picture-in-picture=(), "
    "publickey-credentials-get=(), "
    "screen-wake-lock=(), "
    "sync-xhr=(), "
    "usb=(), "
    "xr-spatial-tracking=()"
)

# HSTS is only meaningful over HTTPS. We detect that by checking
# the X-Forwarded-Proto header (set by Render / Cloudflare / etc.)
# OR the request's url.scheme. Both are honored.
HSTS_VALUE = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response.

    Implementation notes:
    - We only ADD headers; we never strip headers set by the
      application or by FastAPI itself. This means if a route
      intentionally sets (e.g.) `Cache-Control: no-store`, we
      don't clobber it.
    - HSTS is conditional on HTTPS. The check is best-effort:
      - If `X-Forwarded-Proto: https` is set (the standard
        reverse-proxy header), we treat as HTTPS.
      - Else if the request URL itself uses `https://`, we treat
        as HTTPS.
      - Else (plain HTTP, e.g. localhost dev), we skip HSTS.
    - We do NOT set HSTS in DEBUG mode regardless, because
      setting it during development would lock out http://
      localhost browsers for a year on a misconfiguration.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        debug: bool = False,
    ) -> None:
        super().__init__(app)
        self._debug = debug

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Baseline headers — always set.
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
        # `same-origin-allow-popups` (not plain `same-origin`) is
        # required for Firebase popup-based Google sign-in.
        #
        # How Firebase popup auth works:
        #   1. Parent window opens a popup to accounts.google.com
        #   2. After auth, Google redirects popup to
        #      firebaseapp.com/__/auth/handler
        #   3. That handler calls window.opener.postMessage(result,
        #      parentOrigin) to return the ID token to the parent
        #
        # With COOP `same-origin`, the browser SEVERS window.opener
        # for any cross-origin popup, so step 3 fails silently.
        # The popup shows a blank page then closes — indistinguishable
        # from auth/popup-closed-by-user.
        #
        # `same-origin-allow-popups` keeps the opener relationship
        # for popups WE open, while still blocking other origins
        # from navigating into our browsing context. This is the
        # correct value for any app using OAuth popup flows.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        # COEP: omit on /login so the Firebase auth iframe
        # (firebaseapp.com/__/auth/iframe) can load.
        #
        # Firebase Auth popup mode embeds a hidden iframe at
        # firebaseapp.com/__/auth/iframe to relay auth state between
        # the popup and the parent window. That iframe has NO
        # Cross-Origin-Resource-Policy header. Under COEP
        # 'credentialless', iframes without CORP still load in
        # theory, but Chrome blocks them with ERR_BLOCKED_BY_RESPONSE
        # (reason: "origin") at Firebase SDK v10.12.0 — possibly
        # because the iframe itself sends gapi.js requests that
        # conflict with our COEP.
        #
        # The login page is the only place Firebase Auth runs, so we
        # skip COEP there. All other pages keep 'credentialless'.
        if request.url.path != "/login":
            response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"

        # HSTS only when we're confident the request is over HTTPS.
        if not self._debug and self._is_https(request):
            response.headers["Strict-Transport-Security"] = HSTS_VALUE

        return response

    @staticmethod
    def _is_https(request: Request) -> bool:
        """Best-effort detection of HTTPS, honoring reverse-proxy headers."""
        # Standard reverse-proxy header (set by Render, Cloudflare, nginx, etc.)
        forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
        if forwarded_proto == "https":
            return True
        # Fallback: the URL itself. In dev this is http://localhost.
        return request.url.scheme == "https"
