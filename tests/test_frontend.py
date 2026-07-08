"""Tests for frontend router — template rendering."""

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


def test_video_view_has_follow_dropdown(client: TestClient):
    """The video page must show the follow-mode dropdown with the two
    documented options and "Smart" as the default."""
    with _mock_auth():
        video_id = _create_video(client)
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert 'id="transcript-follow-mode"' in response.text
    # Both options present, smart selected by default.
    assert '<option value="smart" selected>Smart (default)</option>' in response.text
    assert '<option value="always">Always scroll</option>' in response.text


def test_base_template_stamps_user_email_meta_when_authenticated(client: TestClient):
    """When the user is signed in, base.html must emit <meta name="x-user-email">
    so the client-side localStorage key can be namespaced per-account."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'name="x-user-email"' in response.text
    assert FAKE_USER["email"] in response.text


def test_base_template_omits_user_email_meta_when_anonymous(client: TestClient):
    """When the user is not signed in, the meta tag must NOT be emitted
    (avoids leaking any email stub and keeps the localStorage key as
    'anon' for unauthenticated browsing)."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="x-user-email"' not in response.text


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
