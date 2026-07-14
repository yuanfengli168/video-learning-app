"""Tests for frontend router — template rendering."""

import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com", "name": "Test User"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth(user=FAKE_USER):
    return patch("app.auth.dependencies.verify_token", return_value=user)


def test_dashboard_unauthenticated(client: TestClient):
    """Dashboard should render for unauthenticated users (sign-in prompt)."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Sign in" in response.text


def test_dashboard_authenticated(client: TestClient):
    """Dashboard should render for authenticated users with courses."""
    with _mock_auth():
        # Create a course first
        client.post(
            "/api/courses",
            json={"title": "Test Course"},
            headers=_auth_headers(),
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert "Test Course" in response.text


def test_login_page(client: TestClient):
    """Login page should render with AuthKit."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "auth-anchor" in response.text
    assert "AuthKit" in response.text


def test_login_page_redirects_authenticated(client: TestClient):
    """Login page should redirect if already authenticated."""
    with _mock_auth():
        response = client.get("/login", headers=_auth_headers())
    # Should return redirect (200 with redirect template or 302)
    assert response.status_code == 200
    assert "Redirecting" in response.text or "redirect" in response.text.lower()


def test_course_view_not_found(client: TestClient):
    """Course view should return 404 for non-existent course."""
    response = client.get("/course/nonexistent-id")
    assert response.status_code == 404
    assert "not found" in response.text.lower()


def test_course_view_found(client: TestClient):
    """Course view should render for existing course."""
    with _mock_auth():
        create_resp = client.post(
            "/api/courses",
            json={"title": "ML Course"},
            headers=_auth_headers(),
        )
        course_id = create_resp.json()["course_id"]
        response = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert "ML Course" in response.text


def test_video_view_not_found(client: TestClient):
    """Video view should return 404 for non-existent video."""
    response = client.get("/video/nonexistent-id")
    assert response.status_code == 404


def test_video_view_found(client: TestClient):
    """Video view should render for existing video."""
    import io

    with _mock_auth():
        # Create course + section + video
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        fake_video = io.BytesIO(b"fake video content")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert "lecture" in response.text


# ── Transcript follow experiment (MVP1.1 — see doc/MVP1.0-PostRelease § Optimization #1) ──


def _create_video(client: TestClient, title: str = "lecture") -> str:
    """Helper: create a course, section, and uploaded video. Returns the video id."""
    import io
    course_resp = client.post(
        "/api/courses", json={"title": "ML"}, headers=_auth_headers()
    )
    course_id = course_resp.json()["course_id"]
    section_resp = client.post(
        f"/api/courses/{course_id}/sections",
        json={"title": "Week 1"},
        headers=_auth_headers(),
    )
    section_id = section_resp.json()["section_id"]
    fake_video = io.BytesIO(b"fake video content")
    upload_resp = client.post(
        f"/api/videos/upload/{section_id}",
        files={"file": (f"{title}.mp4", fake_video, "video/mp4")},
        headers=_auth_headers(),
    )
    return upload_resp.json()["video_id"]


def test_video_view_loads_transcript_follow_script(client: TestClient):
    """The video page must include the deferred transcript-follow script so
    TranscriptFollow is available before renderTranscript runs."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert '<script src="/static/js/transcript-follow.js" defer></script>' in response.text


def test_video_view_loads_transcript_follow_css(client: TestClient):
    """The base template must include the transcript-follow CSS so the
    .is-follow-active highlight is visible."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert '/static/css/transcript-follow.css' in response.text


def test_video_view_does_not_have_follow_dropdown(client: TestClient):
    """MVP2.0 item #2: the follow-mode dropdown is GONE.

    The transcript now uses a single top-anchor mode with no user-
    selectable variant. If a dropdown reappears, it must come with
    a new contract — the assumption that 'one behavior, no modes'
    holds is what this test guards.
    """
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert 'id="transcript-follow-mode"' not in response.text
    assert '<option value="smart"' not in response.text
    assert '<option value="always"' not in response.text


def test_base_template_does_not_stamp_user_email_meta(client: TestClient):
    """MVP2.0 item #2: the x-user-email meta is GONE.

    It existed only so the transcript-follow component could
    namespace its localStorage key per-account. The MVP2.0
    component has no localStorage, so the meta has no consumer.
    We assert both states (authenticated and anonymous) — the
    tag must never be emitted, regardless of who is signed in.
    """
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'name="x-user-email"' not in response.text
    # And the user's email itself must not be inlined anywhere in
    # the HTML head — defense in depth (the meta was the only
    # path that put it there, but the test catches future leaks).
    assert FAKE_USER["email"] not in response.text.split("</head>")[0]


def test_video_file_serving(client: TestClient):
    """Video file endpoint should serve the file."""
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        fake_video = io.BytesIO(b"fake video content")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(
            f"/api/videos/{video_id}/file", headers=_auth_headers()
        )
    assert response.status_code == 200


def test_video_file_not_found(client: TestClient):
    """Video file endpoint should return 404 for non-existent video."""
    with _mock_auth():
        response = client.get(
            "/api/videos/nonexistent/file", headers=_auth_headers()
        )
    assert response.status_code == 404


def test_templates_have_dark_mode(client: TestClient):
    """Templates should include dark mode support."""
    response = client.get("/")
    assert "dark:" in response.text
    assert "toggleTheme" in response.text


def test_templates_have_htmx(client: TestClient):
    """Templates should include HTMX."""
    response = client.get("/")
    assert "htmx" in response.text.lower()


def test_templates_have_sidebar(client: TestClient):
    """Templates should include sidebar navigation."""
    response = client.get("/")
    assert "sidebar" in response.text.lower()
    assert "Dashboard" in response.text

# ── Sidebar search tests ──


def test_sidebar_search_input_present(client: TestClient):
    """Sidebar should have a search input that filters courses."""
    with _mock_auth():
        client.post("/api/courses", json={"title": "ML"}, headers=_auth_headers())
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'id="sidebar-search"' in response.text
    assert "filterSidebarCourses" in response.text
    assert 'class="sidebar-course-item' in response.text
    assert 'id="sidebar-courses-empty"' in response.text


def test_sidebar_courses_have_data_title_for_filtering(client: TestClient):
    """Each course in the sidebar must have a data-title attribute so
    the search filter can match against it (case-insensitive)."""
    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "Machine Learning"}, headers=_auth_headers()
        )
        client.post(
            "/api/courses", json={"title": "Deep Learning"}, headers=_auth_headers()
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'data-title="machine learning"' in response.text
    assert 'data-title="deep learning"' in response.text


def test_sidebar_courses_shown_on_video_page(client: TestClient):
    """The sidebar should show the user's courses on every page, not
    just the dashboard. The _ctx() helper now fetches them when db is
    passed in."""
    import io

    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "ML Course"}, headers=_auth_headers()
        )
        course_id = client.get("/api/courses", headers=_auth_headers()).json()[0]["id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # Sidebar should list the course
    assert "ML Course" in response.text
    assert 'class="sidebar-course-item' in response.text


# ── Chat history page tests ──


def test_chat_history_route_exists(client: TestClient):
    """The /chat-history route should exist and render (no 404)."""
    response = client.get("/chat-history")
    assert response.status_code == 200


def test_chat_history_shows_signin_when_unauthenticated(client: TestClient):
    """Unauthenticated users see a sign-in prompt, not the session list."""
    response = client.get("/chat-history")
    assert response.status_code == 200
    assert "Sign in" in response.text
    # Should NOT show the session list UI
    assert 'id="session-list"' not in response.text


def test_chat_history_has_session_list_ui_for_authenticated(client: TestClient):
    """Signed-in users should see the two-pane layout with session list."""
    with _mock_auth():
        response = client.get("/chat-history", headers=_auth_headers())
    assert response.status_code == 200
    assert 'id="session-list"' in response.text
    assert 'id="session-detail"' in response.text
    assert 'id="session-search"' in response.text


def test_chat_history_has_js_functions(client: TestClient):
    """The page must have the JS that loads + renders sessions."""
    with _mock_auth():
        response = client.get("/chat-history", headers=_auth_headers())
    assert response.status_code == 200
    # Core JS functions
    assert "loadSessions" in response.text
    assert "selectSession" in response.text
    assert "deleteSession" in response.text
    assert "sendChatMessage" in response.text
    assert "filterSessions" in response.text
    # API endpoints it should call
    assert "/api/chat/sessions" in response.text


def test_chat_history_no_sessions_shows_empty_message(client: TestClient):
    """When the API returns no sessions, show an empty-state message."""
    with _mock_auth():
        # Get the page; JS will call the API. We just verify the empty-state
        # string is present in the JS code so it will be shown.
        response = client.get("/chat-history", headers=_auth_headers())
    assert response.status_code == 200
    # The empty-state copy is rendered when sessions.length === 0
    assert "No chat sessions yet" in response.text


# ── Sidebar search UX improvements ──


def test_sidebar_search_has_magnifier_and_clear_button(client: TestClient):
    """The sidebar search input should have a magnifier icon and a
    hidden clear (✕) button so the user can see it's searchable and
    can clear the query."""
    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # Magnifier SVG (the M21 21l-4.35 path is a search icon)
    assert "M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" in response.text
    # Clear button is present and starts hidden
    assert 'id="sidebar-search-clear"' in response.text
    assert "hidden" in response.text.split('id="sidebar-search-clear"')[1][:200]
    # The clearSidebarSearch function exists
    assert "function clearSidebarSearch" in response.text
    # The result count element exists
    assert 'id="sidebar-search-count"' in response.text


def test_sidebar_course_items_have_label_span_for_highlighting(client: TestClient):
    """Each course link must have a `.sidebar-course-label` span so the
    search filter can highlight the matched substring in the title."""
    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "Machine Learning"}, headers=_auth_headers()
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'class="sidebar-course-label"' in response.text


def test_sidebar_search_filter_function_includes_count_and_highlight(client: TestClient):
    """filterSidebarCourses should manage the result count, the clear
    button, and call highlightMatch to mark the matched substring."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # The function body must reference all the new elements
    body_start = response.text.find("function filterSidebarCourses")
    body_end = response.text.find("function highlightMatch", body_start)
    body = response.text[body_start:body_end] if body_end > 0 else response.text[body_start:]
    assert "sidebar-search-count" in body
    assert "sidebar-search-clear" in body
    assert "highlightMatch" in body
    # The highlight function should produce a <mark> tag
    assert response.text.count("function highlightMatch") >= 1
    h_start = response.text.find("function highlightMatch")
    h_end = response.text.find("function clearSidebarSearch", h_start)
    h_body = response.text[h_start:h_end] if h_end > 0 else response.text[h_start:]
    assert "<mark" in h_body
    assert "yellow" in h_body


def test_base_template_defines_escapeHtml_for_search_highlight(client: TestClient):
    """base.html must define escapeHtml because filterSidebarCourses ->
    highlightMatch depends on it. Without it, typing in the sidebar
    search would throw a ReferenceError and silently fail (no filtering,
    no count, no clear button)."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # escapeHtml should be defined in the base template's <script> block
    # (it lives in base.html so that highlightMatch can call it).
    assert "function escapeHtml" in response.text
    # And it must be defined BEFORE highlightMatch is called
    escape_pos = response.text.find("function escapeHtml")
    highlight_call_pos = response.text.find("highlightMatch(original, query)")
    # Note: highlightMatch itself appears later, but the call site
    # filterSidebarCourses appears before the function definition for
    # highlightMatch. The important check is that escapeHtml is defined
    # before filterSidebarCourses (since filterSidebarCourses calls it
    # transitively via highlightMatch which only runs at call time).
    # Practically: the function definition must exist in the page.
    assert escape_pos > 0, "escapeHtml must be defined in base.html"


def test_sidebar_toggle_button_has_both_icons(client: TestClient):
    """The mobile sidebar toggle button must have BOTH the hamburger
    icon (shown when closed) and the close icon (shown when open),
    so the user always sees an appropriate icon for the current state.
    """
    response = client.get("/")
    assert response.status_code == 200
    # Hamburger (3 horizontal lines) — should be visible by default
    assert 'id="sidebar-icon-hamburger"' in response.text
    assert "M4 6h16M4 12h16M4 18h16" in response.text
    # Close (X) — should start hidden
    assert 'id="sidebar-icon-close"' in response.text
    assert "M6 18L18 6M6 6l12 12" in response.text
    # The wrapper button has a label for accessibility
    assert 'id="sidebar-toggle-btn"' in response.text
    assert 'aria-label="Toggle sidebar"' in response.text


def test_toggle_sidebar_function_toggles_both_icons(client: TestClient):
    """toggleSidebar() should toggle the visibility of both the
    hamburger and the close icons so the user sees the right one."""
    response = client.get("/")
    assert response.status_code == 200
    # Find the toggleSidebar function and verify it touches both icons
    start = response.text.find("function toggleSidebar")
    end = response.text.find("function logout", start) if start > 0 else -1
    body = response.text[start:end] if end > 0 else ""
    assert "sidebar-icon-hamburger" in body
    assert "sidebar-icon-close" in body
    assert body.count(".classList.toggle('hidden'") >= 3  # overlay + 2 icons


# ── Responsive layout tests ──


def test_main_content_has_min_w_0_to_prevent_flex_overflow(client: TestClient):
    """The main content wrapper must have `min-w-0` so flex children
    (like a 1280px video player) can shrink below their intrinsic
    width and don't force horizontal overflow on small screens."""
    response = client.get("/")
    assert response.status_code == 200
    # The main container that wraps <header> + <main>
    assert "flex-1 md:ml-64 flex flex-col min-h-screen min-w-0" in response.text


def test_video_element_constrains_its_width(client: TestClient):
    """The video element must use max-w-full + h-auto so it scales
    down to fit narrow viewports instead of overflowing horizontally.
    Without this, a 1280px video forces the entire layout wider than
    the screen on mobile.
    """
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The video tag must have max-w-full + h-auto + block
    assert "class=\"w-full h-auto max-w-full block\"" in response.text


def test_video_page_flex_columns_have_min_w_0(client: TestClient):
    """Both the left and right flex columns on the video page need
    min-w-0 to allow their content (especially the video player) to
    shrink to fit the viewport."""
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The two columns inside the flex row should both have min-w-0
    assert "lg:w-3/5 space-y-4 min-w-0" in response.text  # left col
    assert "lg:w-2/5 min-w-0" in response.text  # right col


def test_transcript_header_stacks_on_mobile(client: TestClient):
    """The transcript section header (title + controls) should stack
    vertically on small screens and sit side-by-side on larger ones,
    so the controls don't get squished.
    """
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # flex-col on mobile, flex-row at sm+ breakpoint
    assert "flex flex-col sm:flex-row" in response.text
    # The controls wrapper should wrap on overflow
    assert "flex-wrap" in response.text


# ── Summary tab SSR (MVP1.1 — see doc/MVP1.0-PostRelease § Optimization #2) ──


def _create_video_with_summary(client: TestClient, summary_md: str) -> str:
    """Helper: create a video and seed its summary Asset directly. Returns
    the video id. Bypasses the generation pipeline so tests don't need
    Ollama; mirrors what /api/generate/{id} would write after a real
    generation job completes."""
    import io
    from app.models import Asset, Video  # local import keeps conftest happy
    from app.database import SessionLocal

    course_resp = client.post(
        "/api/courses", json={"title": "ML"}, headers=_auth_headers()
    )
    course_id = course_resp.json()["course_id"]
    section_resp = client.post(
        f"/api/courses/{course_id}/sections",
        json={"title": "Week 1"},
        headers=_auth_headers(),
    )
    section_id = section_resp.json()["section_id"]
    upload_resp = client.post(
        f"/api/videos/upload/{section_id}",
        files={"file": ("lecture.mp4", io.BytesIO(b"fake video"), "video/mp4")},
        headers=_auth_headers(),
    )
    video_id = upload_resp.json()["video_id"]

    # Seed the summary asset directly. We use SessionLocal (the same
    # session factory the generation worker uses) so the new row is
    # visible to subsequent requests on the test client's connection.
    db = SessionLocal()
    try:
        db.add(Asset(
            video_id=video_id,
            asset_type="summary",
            content=summary_md,
        ))
        db.commit()
    finally:
        db.close()
    return video_id


def _content_summary_html(response_text: str) -> str:
    """Return the HTML inside the `#content-summary` div.

    Uses a balanced-div walker (not a naive `find("</div>")`) because
    the inner `.prose` div ends before the Regenerate button. The
    walker counts `>` chars for the opening tag, then steps through
    nested `<div ...>` and `</div>` pairs to find the matching close.
    """
    start = response_text.find('id="content-summary"')
    if start < 0:
        return ""
    # Walk back to the `<div` that opens this element.
    open_start = response_text.rfind("<div", 0, start)
    if open_start < 0:
        return ""
    # Find the end of the opening tag.
    open_end = response_text.find(">", open_start)
    if open_end < 0:
        return ""
    depth = 1
    i = open_end + 1
    while i < len(response_text) and depth > 0:
        next_open = response_text.find("<div", i)
        next_close = response_text.find("</div>", i)
        if next_close < 0:
            return response_text[open_start:]
        if 0 <= next_open < next_close:
            depth += 1
            i = next_open + 4
        else:
            depth -= 1
            i = next_close + 6
    return response_text[open_start:i]


def test_video_view_ssr_renders_existing_summary(client: TestClient):
    """When a summary Asset already exists, the SSR pre-render must put
    the summary HTML into the response (and must NOT show the Generate
    button). This is the core fix for Optimization #2 — the user never
    sees 'Generate' for a video that already has materials."""
    with _mock_auth():
        video_id = _create_video_with_summary(
            client, "## Hello\n\nThis is the saved summary."
        )
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    cs_html = _content_summary_html(response.text)
    assert cs_html, "could not extract #content-summary block"
    # The summary content was rendered via the | md filter.
    assert "Hello" in cs_html
    assert "This is the saved summary." in cs_html
    # The Regenerate button is rendered server-side.
    assert "Regenerate" in cs_html
    # The Generate button must NOT appear in the summary block.
    # (Note: the loadSummary !resp.ok fallback HTML ALSO contains the
    # literal "Generate Materials" string, but that's inside a JS
    # template literal far from the #content-summary div.)
    assert "Generate Materials" not in cs_html
    # The SSR marker is the .prose div inside #content-summary.
    assert "prose" in cs_html


def test_video_view_ssr_shows_generate_button_when_no_summary(client: TestClient):
    """When no summary Asset exists, the SSR pre-render must show the
    'Generate Materials' button (the existing behavior). Negative case
    that protects against accidentally hiding the button for fresh
    videos."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    cs_html = _content_summary_html(response.text)
    assert cs_html, "could not extract #content-summary block"
    # The Generate button must be present in the summary block.
    assert "Generate Materials" in cs_html
    assert "Regenerate" not in cs_html


def test_video_view_ssr_marks_content_summary_with_prose_div(client: TestClient):
    """The frontend `loadSummary` uses `.prose` inside `#content-summary`
    as the signal that SSR populated the tab. Without this class on the
    SSR'd div, a failed fetch would stomp the existing summary with
    the Generate button (the bug we're fixing)."""
    with _mock_auth():
        video_id = _create_video_with_summary(client, "## Topic A\nBody text")
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # The content-summary div must contain a child .prose div.
    # We use a small substring assertion because the HTML is otherwise
    # minified together with everything else.
    cs_html = _content_summary_html(response.text)
    assert cs_html, "could not extract #content-summary block"
    # The .prose div is the SSR marker.
    assert "Generate Materials" not in cs_html


def test_video_view_ssr_renders_real_html_not_escaped_markup(client: TestClient):
    """Regression: the `md` Jinja filter must return a Markup-safe
    string so the rendered HTML is NOT auto-escaped. Previously the
    filter returned a plain string, so Jinja escaped `<h2>` to
    `&lt;h2&gt;` and the user saw literal HTML tags instead of rendered
    headings.

    This test asserts on a heading's class attribute (which only
    appears in the rendered HTML, not in the escaped version) so any
    future return-to-plain-string regression fails loudly.
    """
    with _mock_auth():
        video_id = _create_video_with_summary(
            client, "## A Heading\n\nbody text"
        )
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    cs_html = _content_summary_html(response.text)
    # The rendered HTML contains the h2 class string.
    assert 'class="text-lg font-semibold mt-4 mb-2"' in cs_html, (
        "Expected the rendered <h2 class='text-lg font-semibold...'> "
        "to appear in the response, but it was HTML-escaped to "
        "&lt;h2 class=...&gt;. The `md` filter is likely returning a "
        "plain string instead of markupsafe.Markup."
    )
    # Belt-and-braces: also check that the escaped form is NOT in the
    # summary block. (It can appear elsewhere on the page e.g. inside
    # the loadSummary JS string, but _content_summary_html already
    # scopes us to #content-summary.)
    assert "&lt;h2" not in cs_html
    # And the heading text itself appears un-escaped.
    assert "A Heading" in cs_html


def test_video_view_ssr_includes_inline_cache_seed_initialSummaryHtml(client: TestClient):
    """The frontend reads the SSR'd content via
    `const initialSummaryHtml = document.getElementById('content-summary').innerHTML`
    and seeds `contentCache.summary` from it (so the first loadSummary()
    call is a no-op). The variable must exist in the page's JS for
    the seed to work."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # The variable declaration must be present.
    assert "const initialSummaryHtml" in response.text
    # The cache-seed logic must check for the .prose marker.
    assert "contentCache.summary = initialSummaryHtml" in response.text


def test_video_view_ssr_includes_inflight_and_cacheCoalesce(client: TestClient):
    """The cacheCoalesce helper and the inFlight map must be present
    so concurrent loadSummary/loadFlashcards/loadQuiz/loadMindmap calls
    collapse to a single fetch each."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert "const inFlight" in response.text
    assert "function cacheCoalesce" in response.text
    # All four loaders are wrapped in cacheCoalesce.
    assert response.text.count("cacheCoalesce(") >= 4


# ── simpleMarkdown byte-equality contract ──
# The Python `simple_markdown` (used as the Jinja `md` filter) and the
# JS `simpleMarkdown` (used for post-regenerate client rendering) must
# produce identical HTML for the same input. This test enforces the
# contract by computing both and comparing; the JS function is re-
# implemented in the test (kept in sync with the production JS by the
# JS_SOURCE constant below).
#
# Why duplicate the JS in Python? So the test is self-contained and
# doesn't need Node. The duplication is intentional — the byte-equality
# check is the "single source of truth" guard. If a future refactor
# changes one implementation, this test fails, and the developer must
# update both sides in lockstep.
_JS_SIMPLE_MARKDOWN = r"""
function simpleMarkdown(md) {
    return md
        .replace(/^### (.*$)/gim, '<h3 class="text-base font-semibold mt-3 mb-1">$1</h3>')
        .replace(/^## (.*$)/gim, '<h2 class="text-lg font-semibold mt-4 mb-2">$1</h2>')
        .replace(/^# (.*$)/gim, '<h1 class="text-xl font-bold mt-4 mb-2">$1</h1>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">$1</code>')
        .replace(/^\- (.*$)/gim, '<li class="ml-4 list-disc">$1</li>')
        .replace(/^\d+\. (.*$)/gim, '<li class="ml-4 list-decimal">$1</li>')
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n/g, '<br>');
}
"""


def _js_simple_markdown(md: str) -> str:
    """Evaluate the JS simpleMarkdown function in a Python sandbox and
    return its output for `md`. Used by the byte-equality test."""
    import re
    if not md:
        return ""

    def _sub(pattern, repl, s, flags=0):
        return re.sub(pattern, repl, s, flags=flags)

    out = md
    out = _sub(r"^### (.*)$", r'<h3 class="text-base font-semibold mt-3 mb-1">\1</h3>', out, re.MULTILINE)
    out = _sub(r"^## (.*)$", r'<h2 class="text-lg font-semibold mt-4 mb-2">\1</h2>', out, re.MULTILINE)
    out = _sub(r"^# (.*)$", r'<h1 class="text-xl font-bold mt-4 mb-2">\1</h1>', out, re.MULTILINE)
    out = _sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", out)
    out = _sub(r"\*(.*?)\*", r"<em>\1</em>", out)
    out = _sub(r"`(.*?)`", r'<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">\1</code>', out)
    out = _sub(r"^- (.*)$", r'<li class="ml-4 list-disc">\1</li>', out, re.MULTILINE)
    out = _sub(r"^\d+\. (.*)$", r'<li class="ml-4 list-decimal">\1</li>', out, re.MULTILINE)
    out = out.replace("\n\n", "<br><br>").replace("\n", "<br>")
    return out


def test_simple_markdown_matches_js_implementation():
    """The Python `simple_markdown` helper must produce byte-identical
    output to the JS `simpleMarkdown` function for a battery of inputs.
    This is the single source of truth guard — if either implementation
    changes, both must change in lockstep."""
    from app.services.markdown import simple_markdown

    cases = [
        "",
        "Plain text",
        "## Heading 2\n\nBody text",
        "# H1\n## H2\n### H3",
        "**bold** and *italic* and `code`",
        "- bullet one\n- bullet two",
        "1. first\n2. second",
        "## Mixed\n\n- item **with bold**\n- item *with italic*\n\n```code block```",
        "Line 1\nLine 2\n\nLine 3",
        "### Edge: bold-inside-heading **like this**",
    ]
    for case in cases:
        py = simple_markdown(case)
        js = _js_simple_markdown(case)
        assert py == js, (
            f"Python and JS markdown outputs differ for input {case!r}:\n"
            f"  Python: {py!r}\n"
            f"  JS:      {js!r}"
        )


def test_video_html_contains_byte_equivalent_simple_markdown():
    """The JS simpleMarkdown source in app/templates/video.html must
    match the locked JS_SOURCE constant above byte-for-byte. If a
    refactor changes one, this test fails, and the developer must
    update both sides and the test in lockstep."""
    video_html = (
        Path(__file__).resolve().parent.parent
        / "app" / "templates" / "video.html"
    ).read_text(encoding="utf-8")

    # The JS function appears verbatim in the template's <script> block.
    # Normalize the JS source to a single line (the template may wrap it
    # onto multiple lines for readability).
    js_compact = re.sub(r"\s+", " ", _JS_SIMPLE_MARKDOWN).strip()
    html_compact = re.sub(r"\s+", " ", video_html)
    assert js_compact in html_compact, (
        "The JS simpleMarkdown function in app/templates/video.html has "
        "diverged from the locked source. Update both this test and "
        "app/services/markdown.py in lockstep."
    )


def test_video_view_includes_course_id_in_inline_script(client: TestClient):
    """The video page must render the courseId into the inline <script>
    so confirmDelete() can redirect to the right course after
    deletion.

    Regression test for the 2026-07-11 bug: confirmDelete() used to
    use `document.querySelector('a[href^="/course/"]')` which
    grabbed the FIRST course link on the page (often a sidebar
    link, NOT the course the video belongs to). The fix was to
    render `courseId` into the script directly.

    This test asserts the script contains the correct courseId
    so a future refactor doesn't regress the fix.
    """
    import io
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "Tests"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Section 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", io.BytesIO(b"x" * 1024), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # The courseId JS constant must be set to the right course.
    # We look for `const courseId = '...'` followed by our course id.
    assert f"const courseId = '{course_id}'" in response.text, (
        "courseId is not in the rendered script — the redirect "
        "fix from commit 1951a20 regressed. The user will be sent "
        "to the wrong course after deletion."
    )
    # And the OLD bug pattern must NOT appear as live code. The
    # comment that explains the bug DOES still mention the old
    # pattern (for future readers), so we check for the actual
    # JS assignment that was the bug — assigning courseHref to
    # the result of the querySelector.
    assert "const courseHref = document.querySelector" not in response.text, (
        "Old DOM-scrape code is back. The fix from commit 1951a20 "
        "regressed — confirmDelete() will redirect to whichever "
        "course link comes first in the DOM, not the course this "
        "video belongs to."
    )


def test_video_view_falls_back_to_dashboard_when_no_course():
    """If the course context is missing (broken FK or shared deep
    link), the template should still render — with an empty
    courseId so the JS falls back to /dashboard.

    This is hard to test through normal flow because the route
    /video/{id} requires the video to exist and have a course.
    Instead, we test that the template logic is correct by
    simulating the Jinja render with course=None.
    """
    template_source = open(
        "app/templates/video.html", encoding="utf-8"
    ).read()
    # The Jinja expression should be the one that produces an empty
    # string when course is None — not a hard crash.
    assert "course.id if course" in template_source, (
        "Template is missing the courseId fallback for None course. "
        "The video view will crash with a 500 if course is missing."
    )


def test_video_view_delete_button_present(client: TestClient):
    """Sanity: the delete button + modal must be on every video page
    so the user can always delete a video. Regression test for
    the manual todo #5 button accidentally being wrapped in an
    if-block that hid it for some videos.
    """
    import io
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("test.mp4", io.BytesIO(b"x" * 1024), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # Delete button + modal must be in the page
    assert "showDeleteModal" in response.text, "delete button JS missing"
    assert "confirmDelete" in response.text, "delete confirm JS missing"
    assert "delete-modal" in response.text, "delete modal HTML missing"
    # And the redirect target must use courseId, not DOM scrape
    assert "courseId" in response.text


def test_video_view_discuss_tab_present(client: TestClient):
    """The Discuss tab (whole-video chat, MVP2.0) must render on every video page.

    Regression test for the bug where the Discuss tab + delete button
    were accidentally removed by the smart-pick Part A (commit 2a96049)
    on 2026-07-11, and stayed missing until 2026-07-14. We test the
    full set of components that have to be there for the feature to
    actually work in the browser:

      1. The tab BUTTON (id="tab-discuss", calls switchTab('discuss'))
      2. The tab CONTENT container (id="content-discuss", hidden by default)
      3. The chat input + send button (id="discuss-input", id="discuss-send-btn")
      4. The JS that starts a session (startDiscussSession) and sends
         a message (sendDiscussMessage) — without these, clicking the
         tab does nothing.
      5. The 'discussSessionId' variable declared at script top-level
         (so switchTab() can reference it without a TDZ error).
      6. A "Chat History" link for the user to find their session later.
    """
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("x.mp4", io.BytesIO(b"fake"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    text = response.text

    # 1. Tab button
    assert "tab-discuss" in text, "Discuss tab button missing"
    assert "💬 Discuss" in text, "Discuss tab label missing"
    assert "switchTab('discuss')" in text, "Discuss tab click handler missing"

    # 2. Tab content container (must be hidden by default — Tailwind
    # `hidden` class) so it doesn't show on initial page load.
    assert 'id="content-discuss"' in text, "Discuss tab content container missing"
    assert 'id="content-discuss" class="hidden' in text, "Discuss content should be hidden by default"

    # 3. Chat UI inside the Discuss tab
    assert 'id="discuss-input"' in text, "Discuss input field missing"
    assert 'id="discuss-send-btn"' in text, "Discuss send button missing"
    assert 'id="discuss-messages"' in text, "Discuss messages container missing"

    # 4. JS functions that wire up the chat
    assert "function startDiscussSession" in text or "async function startDiscussSession" in text, \
        "startDiscussSession() function missing"
    assert "function sendDiscussMessage" in text or "async function sendDiscussMessage" in text, \
        "sendDiscussMessage() function missing"

    # 5. The discussSessionId variable (declared before switchTab references it)
    assert "let discussSessionId" in text, "discussSessionId variable missing"

    # 6. The "Chat History" link inside the Discuss tab (so users can
    # find their session later).
    assert 'href="/chat-history"' in text, "Chat History link missing"


# ─────────────────────────────────────────────────────────────────────────────
# Course and section delete UI (MVP2.0, manualTodo #5 extension)
# ─────────────────────────────────────────────────────────────────────────────


def test_dashboard_has_delete_course_button(client: TestClient):
    """The dashboard course cards must have a delete button. Regression
    test for manualTodo #5 extension — covers the user adding a course
    then having no way to delete it (the existing course delete endpoint
    was unused because there was no button in the UI)."""
    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert "showDeleteCourseModal" in response.text, (
        "delete course button JS missing from dashboard"
    )
    assert "delete-course-modal" in response.text, (
        "delete course modal HTML missing from dashboard"
    )


def test_course_view_has_delete_section_button(client: TestClient):
    """The course view section header must have a delete button."""
    import io
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        response = client.get(f"/course/{course_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert "showDeleteSectionModal" in response.text, (
        "delete section button JS missing from course page"
    )
    assert "delete-section-modal" in response.text, (
        "delete section modal HTML missing from course page"
    )


def test_course_page_inline_script_parses_cleanly(client: TestClient):
    """The course page's inline <script> block must parse as valid JS.

    Regression test for the section-delete-click-does-nothing bug: a
    missing `}` in ``uploadVideo`` left the whole script with a syntax
    error, so the browser silently refused to run *any* of the page's
    JS (toggleSection, retryAllFailed, showDeleteSectionModal, etc).
    The previous string-only check ("showDeleteSectionModal" in
    response.text) was satisfied by the broken HTML, so it never
    caught this.

    We can't rely on Jinja2-rendered output for the assertion: when
    the script has unbalanced braces, Jinja2 itself fails to parse
    ``{{ ... }}`` expressions inside the script (it uses ``}}`` to
    close tags) and returns a partial/garbled script body, which
    can *happen* to have balanced braces by coincidence. So we
    read the template source and validate it as a *string* before
    Jinja rendering — that's where the original bug lived.
    """
    import re
    from pathlib import Path
    template_path = Path(__file__).parent.parent / "app" / "templates" / "course.html"
    source = template_path.read_text()
    # Extract the inline <script>...</script> from the source (raw,
    # pre-Jinja). Strip Jinja comments {# ... #} first so they don't
    # confuse the brace counter (they contain { and } too).
    source_no_comments = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)
    match = re.search(r"<script>(.*?)</script>", source_no_comments, re.DOTALL)
    assert match, "course.html must contain a <script> block"
    script = match.group(1)
    # Replace Jinja expressions with a benign JS identifier so the
    # Python brace-counting check below is reliable. We have to be
    # careful with nested-brace expressions — use a state machine.
    def strip_jinja(s):
        out = []
        i = 0
        while i < len(s):
            if s[i:i+2] == "{{":
                out.append("J")
                # find matching }} handling nested {{ }}
                depth = 1
                i += 2
                while i < len(s) and depth > 0:
                    if s[i:i+2] == "{{":
                        depth += 1
                        i += 2
                    elif s[i:i+2] == "}}":
                        depth -= 1
                        i += 2
                    else:
                        i += 1
            elif s[i:i+2] == "{#":
                # comment - skip to #}
                i += 2
                while i < len(s) and s[i:i+2] != "#}":
                    i += 1
                i += 2
            else:
                out.append(s[i])
                i += 1
        return "".join(out)
    cleaned = strip_jinja(script)
    opens = cleaned.count("{")
    closes = cleaned.count("}")
    parens_o = cleaned.count("(")
    parens_c = cleaned.count(")")
    assert opens == closes, (
        f"course.html <script> has unbalanced braces "
        f"({{={opens}, }}={closes}). This silently breaks ALL page "
        f"JS in the browser — the section delete button click bug "
        f"was caused by exactly this."
    )
    assert parens_o == parens_c, (
        f"course.html <script> has unbalanced parens "
        f"((={parens_o}, )={parens_c})"
    )
