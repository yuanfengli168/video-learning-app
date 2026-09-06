"""Tests for the security headers middleware (app/middleware.py).

Verifies that:
- All baseline security headers are present on every response
  (API, template, static, error)
- HSTS is set when the request is HTTPS
- HSTS is NOT set in DEBUG mode (even if the request is HTTPS)
- HSTS is NOT set when the request is plain HTTP
- The CSP is restrictive enough (no wildcards, no unsafe-eval except
  for the wasm case that's actually needed)
"""

import pytest
from fastapi.testclient import TestClient


# All headers the middleware should set on every response.
EXPECTED_HEADERS = {
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
}


def test_security_headers_on_api_route(client: TestClient):
    """The /api/health endpoint should return all baseline headers."""
    response = client.get("/api/health")
    assert response.status_code == 200
    for header in EXPECTED_HEADERS:
        assert header in response.headers, (
            f"Missing baseline header: {header}. "
            f"Got: {dict(response.headers)}"
        )


def test_security_headers_on_login_page(client: TestClient):
    """The /login template route should return all baseline headers
    except cross-origin-embedder-policy, which is intentionally
    omitted on /login to allow the Firebase auth iframe to load.
    See test_login_page_has_no_coep for the full rationale."""
    response = client.get("/login")
    assert response.status_code == 200
    login_expected = EXPECTED_HEADERS - {"cross-origin-embedder-policy"}
    for header in login_expected:
        assert header in response.headers, f"Missing on /login: {header}"


def test_security_headers_on_404(client: TestClient):
    """Even error responses should have the security headers —
    otherwise an attacker could probe /nonexistent and get a response
    without X-Frame-Options, helping their clickjacking setup."""
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    for header in EXPECTED_HEADERS:
        assert header in response.headers, f"Missing on 404: {header}"


def test_x_frame_options_is_deny(client: TestClient):
    """X-Frame-Options: DENY is the strictest clickjacking protection —
    the app is never legitimately embedded in an iframe."""
    response = client.get("/api/health")
    assert response.headers["x-frame-options"] == "DENY"


def test_x_content_type_options_is_nosniff(client: TestClient):
    """X-Content-Type-Options: nosniff prevents MIME-type sniffing."""
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_referrer_policy_is_strict_origin_when_cross_origin(client: TestClient):
    """Referrer-Policy: strict-origin-when-cross-origin — same-origin
    requests get the full URL (we don't leak anything to ourselves),
    cross-origin HTTPS requests get only the origin, cross-origin
    HTTP→HTTPS downgrade requests get no Referer.

    2026-09-06: was `no-referrer` (zero leak), but YouTube's IFrame
    player rejects embeds with no Referer as "Error 153:
    embedder.identity.missing.referrer" — the embed shows a "Video
    player configuration error" dialog instead of the video. Sending
    at least the origin fixes the embed. The trade-off is acceptable:
    we still don't leak the full URL path to third parties."""
    response = client.get("/api/health")
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_csp_includes_required_origins(client: TestClient):
    """The Content-Security-Policy must allow the CDNs the app
    actually uses (Markmap, htmx, Tailwind, AuthKit) and block
    everything else by default."""
    response = client.get("/api/health")
    csp = response.headers["content-security-policy"]
    # CDNs the app uses (see app/middleware.py CSP comment)
    assert "cdn.jsdelivr.net" in csp, "Markmap CDN must be allowed"
    assert "unpkg.com" in csp, "htmx CDN must be allowed"
    assert "cdn.tailwindcss.com" in csp, "Tailwind CDN must be allowed"
    assert "yuanfengli168.github.io" in csp, "AuthKit CDN must be allowed"
    # gstatic.com is loaded by AuthKit's bundled modules as the
    # Firebase SDK source. Without this allow-list, the login
    # page fails to load Firebase and you can't sign in.
    assert "www.gstatic.com" in csp, (
        "gstatic.com must be allowed (Firebase SDK source used by AuthKit)"
    )
    # Safety: form submissions must only go to our own origin
    assert "form-action 'self'" in csp, "form-action must be restricted to self"
    # Safety: no plugins / Flash / Java applets
    assert "object-src 'none'" in csp, "object-src must be 'none'"


def test_hsts_set_when_request_is_https(client: TestClient):
    """When the reverse proxy reports X-Forwarded-Proto: https, the
    response MUST include Strict-Transport-Security so the browser
    upgrades future requests to HTTPS."""
    response = client.get(
        "/api/health",
        headers={"X-Forwarded-Proto": "https"},
    )
    hsts = response.headers.get("strict-transport-security", "")
    assert hsts, f"HSTS should be set behind HTTPS, got headers: {dict(response.headers)}"
    # Should include the max-age directive (1 year is the OWASP recommendation)
    assert "max-age=" in hsts
    # Should include includeSubDomains (cheap; reduces the chance of
    # a subdomain forgetting to enforce HTTPS)
    assert "includeSubDomains" in hsts


def test_hsts_not_set_over_plain_http(client: TestClient):
    """Over plain HTTP (the dev case), HSTS MUST NOT be set — otherwise
    a misconfigured browser would refuse to talk to localhost for a year."""
    response = client.get("/api/health")
    # No X-Forwarded-Proto header, so this is plain HTTP
    assert "strict-transport-security" not in response.headers, (
        "HSTS should not be set over plain HTTP"
    )


def test_hsts_not_set_in_debug_mode(client: TestClient, monkeypatch):
    """In DEBUG mode (the dev default), HSTS is never set even if the
    request is HTTPS — dev servers should not lock out http://localhost."""
    from app.config import settings
    # Force DEBUG=true for this test, regardless of .env
    monkeypatch.setattr(settings, "debug", True)
    # Need to rebuild the app stack because middleware was added at
    # module import time with the original debug value
    from importlib import reload
    import app.main as main_module
    reload(main_module)
    from fastapi.testclient import TestClient as TC
    debug_client = TC(main_module.app)
    response = debug_client.get(
        "/api/health",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "strict-transport-security" not in response.headers, (
        "HSTS should not be set in DEBUG mode"
    )


def test_csp_disallows_object_embeds(client: TestClient):
    """object-src 'none' blocks <object>, <embed>, <applet> — these
    are legacy vectors for Flash / Java / PDF XSS that we don't use."""
    response = client.get("/api/health")
    csp = response.headers["content-security-policy"]
    assert "object-src 'none'" in csp


def test_csp_has_default_src_self(client: TestClient):
    """default-src 'self' — fall back to same-origin for anything
    we forgot to enumerate. Better than the previous default of
    allowing everything."""
    response = client.get("/api/health")
    csp = response.headers["content-security-policy"]
    assert csp.startswith("default-src 'self'"), (
        f"CSP must start with default-src 'self', got: {csp}"
    )


def test_coop_is_same_origin_allow_popups_not_same_origin(client: TestClient):
    """Cross-Origin-Opener-Policy must be 'same-origin-allow-popups', NOT
    plain 'same-origin'.

    Root cause (2026-07-06): with COOP 'same-origin', the browser SEVERS
    window.opener for ANY cross-origin popup. Firebase popup sign-in uses
    this exact mechanism:
      1. Parent window opens a popup to accounts.google.com
      2. Google redirects popup to firebaseapp.com/__/auth/handler
      3. That handler calls window.opener.postMessage(result, parentOrigin)
         to return the ID token to the parent

    With 'same-origin', step 3 fails silently (window.opener is null).
    The popup shows a blank white page for a few seconds then closes
    with auth/popup-closed-by-user — indistinguishable from a cancelled
    sign-in. Google sign-in was broken the entire time COOP was set to
    'same-origin', but wasn't noticed because the session cookie from
    before the security middleware was added was still valid.

    'same-origin-allow-popups' keeps the opener relationship for popups
    WE open (needed for Firebase auth), while still blocking other origins
    from navigating into our browsing context. This is the correct value
    for any app using OAuth popup flows.
    """
    response = client.get("/api/health")
    coop = response.headers["cross-origin-opener-policy"]
    assert coop == "same-origin-allow-popups", (
        f"COOP must be 'same-origin-allow-popups' (NOT plain 'same-origin'). "
        f"Plain 'same-origin' severs window.opener for cross-origin popups, "
        f"breaking Firebase Google sign-in (popup closes blank with "
        f"auth/popup-closed-by-user). Got: {coop!r}"
    )


def test_coep_is_credentialless_not_require_corp(client: TestClient):
    """Non-login pages must have COEP 'credentialless'.

    The /login page is intentionally exempt — see
    test_login_page_has_no_coep for the rationale.
    """
    response = client.get("/api/health")
    coep = response.headers["cross-origin-embedder-policy"]
    assert coep == "credentialless", (
        f"COEP must be 'credentialless' on non-login pages, got: {coep!r}."
    )


def test_login_page_has_no_coep(client: TestClient):
    """The /login page must NOT set Cross-Origin-Embedder-Policy.

    Firebase Auth popup mode embeds a hidden iframe at
    firebaseapp.com/__/auth/iframe to relay auth state between the
    popup and the parent window. That iframe has no CORP header.
    Under COEP (even 'credentialless'), Chrome blocks it with
    ERR_BLOCKED_BY_RESPONSE (reason: "origin"). Without the iframe,
    signInWithPopup() never resolves and the user can't log in.

    The login page is the only page that runs Firebase Auth, so
    skipping COEP there is targeted and has minimal security impact.
    All other pages keep COEP: credentialless.
    """
    response = client.get("/login")
    assert "cross-origin-embedder-policy" not in response.headers, (
        "COEP must be absent on /login so the Firebase auth iframe "
        "(firebaseapp.com/__/auth/iframe) can load. "
        "Setting COEP on /login blocks Google sign-in with "
        "ERR_BLOCKED_BY_RESPONSE."
    )


def test_permissions_policy_disables_dangerous_features(client: TestClient):
    """Permissions-Policy should disable camera, microphone, geolocation,
    payment, etc. — features the app doesn't use. If a future feature
    needs one, the middleware docstring explains how to re-enable."""
    response = client.get("/api/health")
    pp = response.headers["permissions-policy"]
    # Every dangerous feature should be empty parens (i.e. disabled)
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{feature}=()" in pp, f"Permissions-Policy should disable {feature}"


# ── CSP: Google Sign-In must not be blocked ──────────────────────────────────
# Symptom: clicking "Continue with Google" caused Firebase to throw
# `auth/internal-error` because the browser blocked
# `https://apis.google.com/js/api.js` (and the Google OAuth popup frame)
# under our previous CSP. The script-src/frame-src/child-src must
# whitelist the Google auth domains.


def test_csp_allows_google_signin_script(client: TestClient):
    """script-src must include apis.google.com so the Google Sign-In
    SDK can load. Removing it makes the popup return auth/internal-error."""
    response = client.get("/login")
    csp = response.headers["content-security-policy"]
    assert "https://apis.google.com" in csp, (
        f"script-src/connect-src must include https://apis.google.com for "
        f"Google Sign-In. Current CSP:\n{csp}"
    )


def test_csp_allows_google_oauth_popup_frames(client: TestClient):
    """frame-src/child-src must include accounts.google.com so the OAuth
    popup can render. Without this, Google Sign-In silently fails."""
    response = client.get("/login")
    csp = response.headers["content-security-policy"]
    assert "https://accounts.google.com" in csp, (
        f"frame-src/child-src must include https://accounts.google.com for "
        f"the Google OAuth popup. Current CSP:\n{csp}"
    )


# ── CSP: YouTube catalog player must not be blocked ──────────────────────────
# Symptom (2026-09-06, user-reported): every admin-curated YouTube video
# in the catalog showed a dead black box — the Day-8 yt_player.js wrapper
# loads the IFrame API from www.youtube.com and embeds the player from
# www.youtube-nocookie.com, but neither origin was in the CSP allowlist
# (script-src/frame-src/child-src/connect-src). Silent failure, all users.


def test_csp_allows_youtube_embed_frames(client: TestClient):
    """frame-src/child-src must include the YouTube embed origins or the
    catalog player's iframe is refused by every browser."""
    response = client.get("/api/health")
    csp = response.headers["content-security-policy"]
    assert "https://www.youtube-nocookie.com" in csp, (
        f"frame-src/child-src must include https://www.youtube-nocookie.com "
        f"for the privacy-enhanced embed. Current CSP:\n{csp}"
    )
    assert "https://www.youtube.com" in csp, (
        f"frame-src/child-src must include https://www.youtube.com for the "
        f"IFrame API internals. Current CSP:\n{csp}"
    )


def test_csp_allows_youtube_iframe_api(client: TestClient):
    """script-src must include www.youtube.com for the IFrame API script
    (/iframe_api), and connect-src for its handshake — without them the
    wrapper initializes a player that can't seekTo/play/pause."""
    response = client.get("/api/health")
    csp = response.headers["content-security-policy"]
    assert "https://www.youtube.com" in csp, (
        f"script-src/connect-src must include https://www.youtube.com for "
        f"the IFrame API. Current CSP:\n{csp}"
    )


# ── Permissions-Policy: YouTube iframe needs delegated features ─────────────
# Symptom (2026-09-06, deeper than CSP): even with CSP allow-listing
# the embed origins, the iframe still failed to load with
# `net::ERR_BLOCKED_BY_RESPONSE` and YouTube rendered Error 153
# (embedder.identity.missing.referrer) inside the embed. Root cause:
# the previous Permissions-Policy was `picture-in-picture=()` and
# `fullscreen=(self)` — both **ban** cross-origin iframes from using
# those features, which Chrome treats as fatal for YouTube's embed
# (which declares `allow="...; picture-in-picture"` on the iframe).
# Now those features are delegated to youtube.com + youtube-nocookie.com.


def test_permissions_policy_allows_youtube_picture_in_picture(client: TestClient):
    """Permissions-Policy must delegate picture-in-picture to the
    YouTube origins, otherwise Chrome aborts the embed with
    ERR_BLOCKED_BY_RESPONSE (the iframe declares `allow=...picture-in-picture`
    but the page's policy forbids it)."""
    response = client.get("/api/health")
    pp = response.headers["permissions-policy"]
    assert "picture-in-picture" in pp, "picture-in-picture directive missing"
    assert "youtube-nocookie.com" in pp, (
        f"picture-in-picture must be delegated to youtube-nocookie.com. "
        f"Current Permissions-Policy:\n{pp}"
    )
    assert "youtube.com" in pp, (
        f"picture-in-picture must be delegated to youtube.com. "
        f"Current Permissions-Policy:\n{pp}"
    )


def test_permissions_policy_allows_youtube_fullscreen(client: TestClient):
    """Permissions-Policy must delegate fullscreen to the YouTube
    origins, otherwise the embed's fullscreen button silently no-ops
    AND Chrome may abort the embed entirely (the iframe declares
    `allowfullscreen`)."""
    response = client.get("/api/health")
    pp = response.headers["permissions-policy"]
    assert "fullscreen" in pp, "fullscreen directive missing"
    assert "youtube-nocookie.com" in pp, (
        f"fullscreen must be delegated to youtube-nocookie.com. "
        f"Current Permissions-Policy:\n{pp}"
    )
    assert "youtube.com" in pp, (
        f"fullscreen must be delegated to youtube.com. "
        f"Current Permissions-Policy:\n{pp}"
    )
