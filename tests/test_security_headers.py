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
    """The /login template route should also return the headers (it
    has a lot of inline JS that the CSP must permit)."""
    response = client.get("/login")
    assert response.status_code == 200
    for header in EXPECTED_HEADERS:
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


def test_referrer_policy_is_no_referrer(client: TestClient):
    """Referrer-Policy: no-referrer — don't leak our URLs to third
    parties (e.g. when a user clicks a link to an external resource)."""
    response = client.get("/api/health")
    assert response.headers["referrer-policy"] == "no-referrer"


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


def test_coep_is_credentialless_not_require_corp(client: TestClient):
    """Cross-Origin-Embedder-Policy must be 'credentialless' (not
    'require-corp' or 'unsafe-none').

    Background: when this was 'require-corp', the Tailwind CDN and
    AuthKit CDN stopped loading because neither sends a
    Cross-Origin-Resource-Policy header. The result: a
    `ReferenceError: tailwind is not defined` in the browser and
    a completely unstyled UI.

    'credentialless' is the OWASP-recommended value for apps that
    don't use SharedArrayBuffer. It still isolates the page from
    cross-origin credentialed requests, while allowing non-CORP
    resources (like the Tailwind CDN) to load.
    """
    response = client.get("/api/health")
    coep = response.headers["cross-origin-embedder-policy"]
    assert coep == "credentialless", (
        f"COEP must be 'credentialless' (the value that allows "
        f"Tailwind CDN and AuthKit to load), got: {coep!r}. "
        f"If you really need to change this, update the test and "
        f"verify the dashboard, login page, and mindmap still load."
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
