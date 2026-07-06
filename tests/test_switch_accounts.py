"""Regression test for the 'can't switch accounts' bug.

Repro: login as user A, logout, login as user B, then load any
authenticated page. Expected: page shows user B's identity.
Actual (before fix): page shows user A's identity — the old cookie
was never cleared, so the new POST /api/auth/session with user B's
token silently kept the old token in the browser's cookie jar (or
the new cookie was set but the old cached user was rendered first).

We exercise this at the backend level: simulate the cookie sequence
that the browser would see and assert the server resolves the
identity to whichever token is in the LATEST cookie value.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

USER_A = {"uid": "user-A-uid", "email": "alice@example.com", "name": "Alice"}
USER_B = {"uid": "user-B-uid", "email": "bob@example.com", "name": "Bob"}
TOKEN_A = "fake-token-for-alice"
TOKEN_B = "fake-token-for-bob"


def test_switch_accounts_via_new_session_cookie(client: TestClient):
    """After login/logout/login with a different account, /api/auth/me
    should return the NEW user, not the old one."""
    # 1) Sign in as Alice — sets fb_token=TOKEN_A
    with patch("app.auth.session.verify_token", return_value=USER_A):
        resp = client.post("/api/auth/session", json={"id_token": TOKEN_A})
    assert resp.status_code == 200

    # Verify cookie was set to TOKEN_A and /me returns Alice
    cookies = client.cookies
    assert cookies.get("fb_token") == TOKEN_A

    with patch("app.auth.dependencies.verify_token", return_value=USER_A):
        me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["uid"] == "user-A-uid"

    # 2) Logout — should clear the cookie
    resp = client.delete("/api/auth/session")
    assert resp.status_code == 200
    # httpx TestClient should now have no fb_token
    assert cookies.get("fb_token") is None or cookies.get("fb_token") == ""

    # 3) Sign in as Bob — should set fb_token=TOKEN_B
    with patch("app.auth.session.verify_token", return_value=USER_B):
        resp = client.post("/api/auth/session", json={"id_token": TOKEN_B})
    assert resp.status_code == 200
    assert cookies.get("fb_token") == TOKEN_B

    # 4) /me MUST return Bob now (not Alice)
    with patch("app.auth.dependencies.verify_token", return_value=USER_B):
        me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["uid"] == "user-B-uid", (
        f"After logout + login as Bob, /me should return Bob's uid, "
        f"but got {me.json()}"
    )


def test_logout_clears_cookie_completely(client: TestClient):
    """After DELETE /api/auth/session, the response's Set-Cookie header
    MUST contain an expired cookie (Max-Age=0 or an Expires date in the
    past). Otherwise the browser may keep the old value."""
    # First set a session
    with patch("app.auth.session.verify_token", return_value=USER_A):
        client.post("/api/auth/session", json={"id_token": TOKEN_A})

    # Then logout
    resp = client.delete("/api/auth/session")
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    # The cookie must be marked for deletion — look for either Max-Age=0
    # or an Expires= that is in the past (year < current year).
    assert "fb_token" in set_cookie, (
        f"Set-Cookie header should mention fb_token to clear it, got: {set_cookie}"
    )
    lowered = set_cookie.lower()
    # FastAPI's delete_cookie sets max-age=0
    assert "max-age=0" in lowered or "expires=" in lowered, (
        f"Logout should mark the cookie as expired, got: {set_cookie}"
    )


def test_new_session_overwrites_old_cookie(client: TestClient):
    """POSTing /api/auth/session with a new token MUST overwrite any
    existing fb_token cookie (not append or duplicate it)."""
    # Initial login as Alice
    with patch("app.auth.session.verify_token", return_value=USER_A):
        client.post("/api/auth/session", json={"id_token": TOKEN_A})
    assert client.cookies.get("fb_token") == TOKEN_A

    # Login as Bob WITHOUT explicit logout — should overwrite
    with patch("app.auth.session.verify_token", return_value=USER_B):
        resp = client.post("/api/auth/session", json={"id_token": TOKEN_B})
    assert resp.status_code == 200
    assert client.cookies.get("fb_token") == TOKEN_B, (
        f"New session should overwrite the old cookie, got: "
        f"{client.cookies.get('fb_token')}"
    )


# ── Frontend regression test for the "can't switch accounts" bug ──────────

def test_login_page_forces_signout_before_subscribing(client: TestClient):
    """Regression test for the bug where logging in as a new account
    after logout silently re-signed-in the previous account.

    Root cause was AuthKit re-hydrating the previous user from Firebase
    IndexedDB on /login page load, then our onAuthStateChanged handler
    POSTing /api/auth/session with the old user's token and redirecting
    to / before the user had a chance to sign in with the new account.

    The fix forces a signOut() and DELETE /api/auth/session on /login
    page load BEFORE subscribing to onAuthStateChanged, so the first
    callback is the post-signOut null state (not the cached user). As
    a belt-and-suspenders, the handler also gates the redirect behind
    a `userInitiatedSignIn` flag that only flips on a real click inside
    the auth-anchor.
    """
    response = client.get("/login")
    assert response.status_code == 200
    text = response.text

    # 1) Must call AuthKit.signOut() before subscribing to
    #    onAuthStateChanged. This clears any Firebase-IndexedDB-cached
    #    user so the first onAuthStateChanged event fires with `null`
    #    instead of the previous user.
    assert "AuthKit.signOut" in text, (
        "login.html must call AuthKit.signOut() to clear any cached "
        "Firebase user before showing the login form. Without this, "
        "the previous user's session is re-hydrated from IndexedDB "
        "and the user is silently signed back in as the old account."
    )

    # 2) Must also clear the backend cookie in case a previous logout
    #    failed to reach the server (e.g. closed tab before DELETE
    #    completed).
    assert "method: 'DELETE'" in text or 'method:"DELETE"' in text, (
        "login.html must DELETE /api/auth/session on page load so a "
        "stale cookie can't be reused."
    )

    # 3) Must track a user-initiated sign-in flag, so the redirect
    #    to '/' only happens after a real click on a sign-in button,
    #    not on the initial cache re-hydration.
    #    We use a simple click listener (NOT stopImmediatePropagation or
    #    direct signInWithPopup interception — those create a race with
    #    AuthKit's own popup handler and cause auth/cancelled-popup-request).
    assert "userInitiatedSignIn" in text, (
        "login.html must gate the post-signin redirect behind a flag "
        "that only flips on an explicit user click. "
        "Do NOT intercept the click with stopImmediatePropagation or "
        "call signInWithPopup directly — that races with AuthKit's own "
        "popup flow and causes auth/cancelled-popup-request."
    )


def test_logout_awaits_signout_before_navigating(client: TestClient):
    """The base.html logout() function must await AuthKit.signOut()
    BEFORE calling window.location.href = '/login'. Otherwise there's
    a race: the navigation kicks off while Firebase is still in the
    authenticated state, so AuthKit re-hydrates the old user on the
    /login page before our signOut can complete.
    """
    response = client.get("/")
    assert response.status_code == 200
    text = response.text

    # Find the logout() function body
    import re
    m = re.search(
        r"async function logout\(\).*?\n(.*?)\n        \}",
        text,
        re.DOTALL,
    )
    assert m, "logout() function not found in base.html"
    body = m.group(1)

    # Must await AuthKit.signOut()
    assert "await AuthKit.signOut" in body, (
        "logout() must await AuthKit.signOut() so the Firebase session "
        "is fully cleared before navigating to /login."
    )

    # The cookie DELETE should happen before signOut (so a signOut
    # failure doesn't leave a stale server cookie). The logout function
    # has exactly one DELETE call, and that's the one for the session
    # cookie.
    import re as _re
    delete_match = _re.search(
        r"fetch\('/api/auth/session',\s*\{\s*method:\s*'DELETE'\s*\}",
        body,
    )
    # Find the LAST occurrence of `await AuthKit.signOut(` — that's the
    # actual call, not the comments. The function may mention the name
    # in comments before the real call.
    signout_calls = list(_re.finditer(r"AuthKit\.signOut\s*\(\s*\)", body))
    assert delete_match is not None, (
        "logout() must DELETE /api/auth/session"
    )
    assert signout_calls, "logout() must call AuthKit.signOut()"
    # Use the actual function call (last match in body) for ordering.
    signout_pos = signout_calls[-1].start()
    assert delete_match.start() < signout_pos, (
        "logout() should DELETE the server cookie BEFORE calling "
        "AuthKit.signOut(), so a signOut failure doesn't leave a "
        "stale server cookie. Got delete_pos=%d, signout_pos=%d"
        % (delete_match.start(), signout_pos)
    )
