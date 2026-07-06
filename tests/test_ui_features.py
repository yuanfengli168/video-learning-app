"""Tests for frontend template UI features — search nav, summary loading, mindmap button."""

from unittest.mock import patch

from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com", "name": "Test"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def test_video_page_has_transcribe_button(client: TestClient):
    """Video page should have a Transcribe button."""
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
    assert "transcribe-btn" in response.text
    assert "Transcribe" in response.text


def test_video_page_has_whisper_model_selector(client: TestClient):
    """Video page should have a Whisper model dropdown."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "whisper-model" in response.text
    assert "tiny" in response.text
    assert "base" in response.text
    assert "small" in response.text
    assert "medium" in response.text


def test_video_page_has_search_navigation(client: TestClient):
    """Video page should have search navigation (prev/next/clear)."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "search-nav" in response.text
    assert "searchNavigate" in response.text
    assert "clearSearch" in response.text


def test_video_page_has_mindmap_fullscreen_button(client: TestClient):
    """Video page should have a mindmap fullscreen expand button."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "openMindmapFullscreen" in response.text
    assert "closeMindmapFullscreen" in response.text
    assert "Expand to Full Screen" in response.text


def test_video_page_has_summary_loading_function(client: TestClient):
    """Video page should have loadSummary function."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "loadSummary" in response.text
    assert "simpleMarkdown" in response.text


def test_dashboard_has_create_course_js(client: TestClient):
    """Dashboard should use JS fetch for course creation (not HTMX)."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())

    assert response.status_code == 200
    assert "createCourse" in response.text
    # Should NOT have hx-post for course creation
    assert "hx-post" not in response.text


def test_course_page_has_upload_function(client: TestClient):
    """Course page should have uploadVideo function."""
    import io

    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        response = client.get(f"/course/{course_id}", headers=_auth_headers())

    assert response.status_code == 200
    assert "uploadVideo" in response.text
    assert "createSection" in response.text

def test_video_page_has_topic_banner(client: TestClient):
    """Video page should have a topic notification banner (hidden by default)."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Banner element
    assert "topic-banner" in response.text
    assert "topic-banner-name" in response.text
    assert "topic-banner-time" in response.text


def test_video_page_has_topic_click_functions(client: TestClient):
    """Video page should have JS functions for clicking mindmap topics."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Required functions for the topic click flow
    assert "function jumpToTopic" in response.text
    assert "function showTopicBanner" in response.text
    assert "function closeTopicBanner" in response.text
    assert "function highlightTranscriptRange" in response.text
    assert "function findTopicTimestamp" in response.text
    assert "function attachMindmapClickHandler" in response.text
    # Should fetch topic_timestamps asset
    assert "/assets/topic_timestamps" in response.text
    assert "topicTimestamps" in response.text


def test_video_page_preloads_markmap_script(client: TestClient):
    """Video page should pre-load the Markmap CDN script on page load to
    avoid the multi-second cold start when the user first clicks the
    mindmap tab."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The page should pre-load the Markmap script
    assert "preloadMarkmapScript" in response.text
    assert "markmap-autoloader" in response.text
    # Loading state should be shown to the user (so the tab is never blank)
    assert "mindmap-loading" in response.text or "Loading mindmap" in response.text


def test_mindmap_node_has_click_tooltip_and_pointer_cursor(client: TestClient):
    """Mindmap nodes should show a pointer cursor and a tooltip saying
    'Click to watch this part of the video'."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Pointer cursor
    assert "node.style.cursor = 'pointer'" in response.text
    # Tooltip
    assert "Click to watch this part of the video" in response.text
    # jumpToTopic should close the fullscreen modal first
    assert "closeMindmapFullscreen()" in response.text


def test_video_page_has_mindmap_parent_map_ancestor_lookup(client: TestClient):
    """When a user clicks a leaf node that has no exact timestamp, the
    page should walk up the mindmap tree to find the closest ancestor
    that does. This prevents the 'No timestamp info' error for deeply
    nested topics that the LLM didn't enumerate directly.
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Parent map builder and ancestor walker must exist
    assert "function buildMindmapParentMap" in response.text
    assert "function findTopicTimestampWithAncestors" in response.text
    # The parent map should be populated when the mindmap loads
    assert "mindmapParentMap = buildMindmapParentMap" in response.text
    assert "mindmapParentMap" in response.text
    # jumpToTopic must call the ancestor-aware version
    assert "findTopicTimestampWithAncestors(topicName, mindmapParentMap)" in response.text


def test_video_page_has_graceful_toast_not_alert(client: TestClient):
    """The page should show a non-blocking toast (not an alert) when no
    timestamp is found, so the user can keep interacting with the mindmap.
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # Toast helper exists
    assert "function showToast" in response.text
    # jumpToTopic should call showToast, not alert
    assert "showToast(" in response.text
    # Old alert() with 'No timestamp info' should be gone
    assert "alert(`No timestamp info" not in response.text


def test_topic_banner_is_between_video_and_transcript(client: TestClient):
    """The topic banner should be placed BELOW the video player and
    ABOVE the transcript, so the user can see it without scrolling to
    the top of the page on small screens.
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    text = response.text
    # The video player element appears before the topic banner
    video_pos = text.find('<video controls')
    banner_pos = text.find('id="topic-banner"')
    transcript_pos = text.find('id="transcript-container"')
    assert video_pos > 0, "video player not found"
    assert banner_pos > 0, "topic banner not found"
    assert transcript_pos > 0, "transcript container not found"
    # The banner must be BETWEEN the video and the transcript
    assert video_pos < banner_pos < transcript_pos, (
        f"topic banner must be between video ({video_pos}) and transcript "
        f"({transcript_pos}), but is at position {banner_pos}"
    )


def test_show_topic_banner_does_not_scroll_to_top(client: TestClient):
    """When a mindmap node is clicked, the banner appears between the
    video and transcript, so we should scroll the BANNER into view (not
    the top of the page)."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # showTopicBanner should scrollIntoView on the banner itself, not the window
    assert "banner.scrollIntoView" in response.text
    # And the old "window.scrollTo({top: 0" should be gone from showTopicBanner
    # (it's only used elsewhere, e.g. for chat).
    # Find the showTopicBanner function body and verify it doesn't scroll window to top
    import re
    m = re.search(
        r"function showTopicBanner\(.*?\n(.*?)\n}",
        response.text,
        re.DOTALL,
    )
    assert m, "showTopicBanner function not found"
    body = m.group(1)
    assert "scrollIntoView" in body
    # Verify it doesn't scroll the window to top
    assert "window.scrollTo({top: 0" not in body


def test_open_mindmap_fullscreen_auto_fits(client: TestClient):
    """The fullscreen mindmap should auto-fit on open so the user sees
    the full mindmap immediately (not the top-left corner)."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The fullscreen function should use the same Markmap class direct
    # approach (not the autoloader template pattern) so we can wait for
    # the SVG to be created and then fit it.
    import re
    m = re.search(
        r"function openMindmapFullscreen\(\).*?\n(.*?)\nfunction ",
        response.text,
        re.DOTALL,
    )
    assert m, "openMindmapFullscreen function not found"
    body = m.group(1)
    # It should use Markmap.create directly (not autoloader template)
    assert "Markmap.create(svg, null, root)" in body, "should use Markmap.create directly"
    # It should call mm.fit() to auto-fit
    assert "mm.fit()" in body, "should auto-fit the mindmap"
    # It should NOT use the autoloader template pattern in the fullscreen
    assert 'type="text/template"' not in body, "should not use autoloader template"


def test_close_mindmap_fullscreen_refits_inline(client: TestClient):
    """When the user closes the fullscreen mindmap, the inline mindmap
    should be re-fit in case the body layout shifted (e.g., scrollbar
    appeared/disappeared)."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The closeMindmapFullscreen function should refit the inline mindmap
    import re
    m = re.search(
        r"function closeMindmapFullscreen\(\).*?\n(.*?)\nfunction ",
        response.text,
        re.DOTALL,
    )
    assert m, "closeMindmapFullscreen function not found"
    body = m.group(1)
    # It should reference the inline mindmap instance and fit it
    assert "mindmapInstances['inline']" in body, "should refit inline mindmap on close"
    assert ".fit()" in body, "should call .fit() on the inline mindmap"


def test_mindmap_refits_on_window_resize(client: TestClient):
    """The inline mindmap should re-fit when the window is resized,
    because Tailwind breakpoints can change the container's width."""
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
        fake_video = io.BytesIO(b"fake")
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", fake_video, "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())

    assert response.status_code == 200
    # The page should have a window resize listener that re-fits the mindmap
    assert "addEventListener('resize'" in response.text


def test_inline_mindmap_attach_interaction_for_pan_and_zoom(client: TestClient):
    """The inline mindmap tab must attach the drag/pan/scroll-zoom
    interaction (not just the click handler). Previously only the
    fullscreen view had pan, so users couldn't drag the inline mindmap
    to see off-screen nodes."""
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
    # Find the renderMindmap function body and verify it calls
    # attachMindmapInteraction (not just attachMindmapClickHandler).
    import re
    m = re.search(
        r"function renderMindmap\(markdown\).*?\n(.*?)\nfunction fitMindmapSVG",
        response.text,
        re.DOTALL,
    )
    assert m, "renderMindmap function not found"
    body = m.group(1)
    # The inline render path must call attachMindmapInteraction so the
    # user can pan and zoom the inline mindmap (previously only the
    # fullscreen view had this).
    assert "attachMindmapInteraction(container)" in body, (
        "renderMindmap should call attachMindmapInteraction for the "
        "inline mindmap so users can pan/zoom it"
    )


def test_render_mindmap_does_not_refit_after_300ms(client: TestClient):
    """renderMindmap used to call mm.fit() again at 300ms as a safety
    net, which would re-center the mindmap and blow away any pan/zoom
    the user did in the first 300ms. That safety net has been removed."""
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
    import re
    m = re.search(
        r"function renderMindmap\(markdown\).*?\n(.*?)\nfunction fitMindmapSVG",
        response.text,
        re.DOTALL,
    )
    assert m
    body = m.group(1)
    # The old safety-net re-fit at 300ms should be gone.
    assert "setTimeout(() => {" not in body or "}, 300)" not in body, (
        "renderMindmap should not have a 300ms safety-net re-fit that "
        "blows away the user's pan/zoom"
    )


def test_attach_mindmap_interaction_uses_per_container_state(client: TestClient):
    """attachMindmapInteraction must use per-container state (stored on
    the DOM node), so the inline and fullscreen views have independent
    drag state. Previously they shared module-level variables, which
    caused the two views to interfere with each other when both open."""
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
    import re
    m = re.search(
        r"function attachMindmapInteraction\(container\)(.*?)\nfunction fitMindmapFullscreen",
        response.text,
        re.DOTALL,
    )
    assert m, "attachMindmapInteraction not found"
    body = m.group(1)
    # Must use container.__mindmapDrag (per-container state)
    assert "container.__mindmapDrag" in body, (
        "attachMindmapInteraction should use container.__mindmapDrag "
        "for per-container state"
    )
    # Must not have module-level isDragging/startX/startY variables
    assert "let isDragging" not in body
    assert "let startX" not in body
    assert "let startY" not in body


def test_resize_listener_skips_when_size_unchanged(client: TestClient):
    """The window resize listener should skip the re-fit if the
    inline mindmap container's size hasn't actually changed. Otherwise
    every scrollbar appearance triggers a re-fit that blows away the
    user's pan/zoom."""
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
    # Find the resize handler
    import re
    m = re.search(
        r"window\.addEventListener\('resize'.*?\}\);",
        response.text,
        re.DOTALL,
    )
    assert m, "resize listener not found"
    body = m.group(0)
    # Must track the last size and skip if unchanged
    assert "_lastInlineSize" in body, (
        "resize listener should track _lastInlineSize and skip if "
        "the size hasn't actually changed"
    )
    assert "w === _lastInlineSize.w && h === _lastInlineSize.h" in body


def test_dashboard_upload_zone_shows_section_picker_when_sections_exist(client: TestClient):
    """When the user has courses with sections, the dashboard upload
    zone should show a section picker dropdown instead of the
    'create a course first' alert."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # Section picker should be present
    assert 'id="dashboard-upload-section"' in response.text
    # Section id should appear as an option
    # (Use the course id in section option since section_ids are uuid-like)
    assert "ML / Week 1" in response.text
    # The Choose Video button is there
    assert 'id="dashboard-upload-btn"' in response.text
    # No "create a course first" message
    assert "create a course and section first" not in response.text


def test_dashboard_upload_zone_shows_help_when_no_sections(client: TestClient):
    """When the user has a course but no sections, the dashboard
    upload zone should show a 'create a section' hint linking to the
    course page (not the misleading 'create a course' message)."""
    with _mock_auth():
        client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # No section picker
    assert 'id="dashboard-upload-section"' not in response.text
    # Helpful hint about creating a section, with a link to the course
    assert "Create a section" in response.text
    assert "Go to ML" in response.text


def test_dashboard_upload_zone_shows_create_course_when_no_courses(client: TestClient):
    """When the user has no courses, the dashboard upload zone should
    show the 'create a course' hint with a button."""
    with _mock_auth():
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # No section picker
    assert 'id="dashboard-upload-section"' not in response.text
    # Helpful hint
    assert "Create a course" in response.text
    # onclick that opens the create-course form
    assert "showCreateCourse" in response.text


def test_dashboard_has_real_upload_function_not_alert(client: TestClient):
    """The dashboard must have a real uploadToSection function (not
    the old stub that just showed an alert). Also: it must support
    drag-and-drop."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    # Real upload function
    assert "function uploadToSection" in response.text
    # Calls the real upload endpoint
    assert "/api/videos/upload/" in response.text
    # Drag-and-drop handlers — the page wires up dragover, dragleave,
    # and drop via a forEach loop, so we just check that the event
    # names are present.
    assert "'drop'" in response.text
    assert "'dragover'" in response.text
    assert "'dragleave'" in response.text
    # No more old stub
    assert "Please create a course and section first" not in response.text
    assert "function handleUpload" not in response.text


def test_dashboard_upload_status_element_exists(client: TestClient):
    """A #dashboard-upload-status element must exist so the user
    gets feedback during/after upload (success or failure)."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"}, headers=_auth_headers()
        )
        course_id = course_resp.json()["course_id"]
        client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Week 1"},
            headers=_auth_headers(),
        )
        response = client.get("/", headers=_auth_headers())
    assert response.status_code == 200
    assert 'id="dashboard-upload-status"' in response.text
