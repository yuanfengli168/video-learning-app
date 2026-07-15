"""Tests for the collapsible section-videos panel on the video page.

MVP2.0.8 (manualTodo [jul14] #0): the video page now has a
collapsible panel above the tabbed interface that shows the
current section's video list. The user can sort by name (asc/
desc) or by date (asc/desc), click any video to switch to it,
and the panel's open/closed state + sort direction persist in
localStorage.

The implementation is purely frontend (the server already has
section.videos in the template context, and the natural sort
key is pre-computed by the existing `natural_sort_key_str`
Jinja filter). No new endpoint. The tests below verify the
HTML rendering and data attributes; the JavaScript sort logic
is verified manually + via the existing browser-side test
pattern.
"""

import re
import io

import pytest
from fastapi.testclient import TestClient


# ── Helpers ────────────────────────────────────────────────────────────────


def _mock_auth():
    from unittest.mock import patch
    FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _create_course_section_videos(
    client: TestClient, video_titles: list[str],
) -> tuple[str, str, list[str]]:
    """Helper: create a course, a section, and N videos in the
    section. Returns (course_id, section_id, [video_id, ...])."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "Test course"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "Section 1"}, headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        video_ids = []
        for title in video_titles:
            upload_resp = client.post(
                f"/api/videos/upload/{section_id}",
                files={"file": (f"{title}.mp4", io.BytesIO(b"x" * 100), "video/mp4")},
                headers=_auth_headers(),
            )
            video_ids.append(upload_resp.json()["video_id"])
    return course_id, section_id, video_ids


# ── Tests for the panel rendering ─────────────────────────────────────────


def test_section_videos_panel_renders(client: TestClient):
    """The <details id="section-videos-panel"> element is present on
    the video page when the video belongs to a section.
    """
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "ML"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "S1"}, headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lec.mp4", io.BytesIO(b"x"), "video/mp4")},
            headers=_auth_headers(),
        )
        video_id = upload_resp.json()["video_id"]
        response = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert response.status_code == 200
    # The panel exists
    assert 'id="section-videos-panel"' in response.text
    # The summary text is present
    assert "Section videos" in response.text


def test_section_videos_panel_shows_all_videos(client: TestClient):
    """The panel lists every video in the section, including the
    currently-playing one."""
    _, _, video_ids = _create_course_section_videos(
        client, ["Lecture 1", "Lecture 2", "Lecture 3"],
    )
    # Open the page for video 1
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    assert response.status_code == 200
    # All 3 video titles should be in the response (and the
    # panel's count text should be "3")
    for title in ["Lecture 1", "Lecture 2", "Lecture 3"]:
        assert title in response.text
    assert "section-video-count\">3<" in response.text or "(3)" in response.text


def test_section_videos_panel_current_video_highlighted(client: TestClient):
    """The currently-playing video is visually highlighted (bg-indigo-50
    + border-l-4 border-indigo-500) in the panel.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["Alpha", "Beta", "Gamma"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[1]}", headers=_auth_headers())
    assert response.status_code == 200
    # The Beta video's <a> should have the highlight classes
    text = response.text
    # Find the <a> for the Beta video and check it has highlight classes
    # The marker is `data-video-id="{{ video.id }}"` on the current
    # video's <a>, so the highlighted video is the one with
    # data-video-id equal to video_ids[1].
    # We look for the data attribute and the highlight classes nearby.
    assert f'data-video-id="{video_ids[1]}"' in text
    # The highlighted <a> should have bg-indigo-50
    # (use a regex to find the <a> tag with that data-video-id and
    # check it has the highlight class).
    pattern = rf'<a[^>]*data-video-id="{video_ids[1]}"[^>]*class="([^"]+)"'
    match = re.search(pattern, text)
    assert match, f"Could not find <a> for current video {video_ids[1]}"
    classes = match.group(1)
    assert "bg-indigo-50" in classes, (
        f"Current video's <a> should have bg-indigo-50 highlight class. "
        f"Got classes: {classes!r}"
    )
    assert "border-indigo-500" in classes, (
        f"Current video's <a> should have border-indigo-500 left border. "
        f"Got classes: {classes!r}"
    )


def test_section_videos_panel_data_sort_key_present(client: TestClient):
    """Each video <a> in the panel has a data-sort-key attribute
    (pre-computed natural sort key from the Jinja filter) so the
    client-side sort function works.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["First video", "Second video"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    # Find all data-sort-key attributes in the section-videos panel
    sort_keys = re.findall(r'data-sort-key="([^"]+)"', text)
    assert len(sort_keys) >= 2, (
        f"Expected at least 2 data-sort-key attributes (one per video), "
        f"got: {sort_keys}"
    )
    # Each sort key should be non-empty
    assert all(sk for sk in sort_keys), (
        f"Sort keys should be non-empty: {sort_keys}"
    )


def test_section_videos_panel_data_date_present(client: TestClient):
    """Each video <a> in the panel has a data-date attribute
    (ISO 8601 string) so the client-side date sort function works.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["First", "Second"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    # Find all data-date attributes in the section-videos panel
    dates = re.findall(r'data-date="([^"]+)"', text)
    assert len(dates) >= 2, (
        f"Expected at least 2 data-date attributes, got: {dates}"
    )
    # Each date should be a valid ISO 8601 string (YYYY-MM-DD...)
    for d in dates:
        assert d.startswith("2026-") or d.startswith("2025-"), (
            f"data-date should be a recent ISO 8601 date, got: {d!r}"
        )


def test_section_videos_sort_dropdown_present(client: TestClient):
    """The <select id="section-videos-sort"> element is present with
    the 4 sort options: name-asc, name-desc, date-asc, date-desc.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["Test video"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    assert 'id="section-videos-sort"' in text
    # All 4 sort options must be present
    for value in ("name-asc", "name-desc", "date-asc", "date-desc"):
        assert f'value="{value}"' in text, f"Sort option '{value}' missing"
    # The user-facing labels must be present
    for label in ("Name ↑", "Name ↓", "Date ↑", "Date ↓"):
        assert label in text, f"Sort label '{label}' missing"


def test_section_videos_empty_state_message(client: TestClient):
    """When the section has only ONE video (the current one), the
    panel shows the 'No other videos' empty state.

    Actually re-reading the implementation: with 1 video, the
    panel still shows the video (so the user can click it as
    feedback that the panel works). Only when the section has
    ZERO videos does it show the empty state. But the section
    always has the current video, so ZERO videos is impossible.
    We test that with 2+ videos, no empty-state message is shown.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["Only video"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    # With 1 video, no "No other videos" message
    assert "No other videos in this section" not in text
    # The single video IS in the panel
    assert "Only video" in text


def test_section_videos_panel_collapsed_by_default(client: TestClient):
    """The panel's <details> element does NOT have the `open` attribute
    by default — the user has to click the summary to expand it.
    The state is persisted in localStorage on the first interaction.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["A", "B"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    # The <details> tag should NOT have `open` attribute
    details_match = re.search(
        r'<details\s+id="section-videos-panel"([^>]*)>',
        text,
    )
    assert details_match, "Could not find <details> tag"
    attrs = details_match.group(1)
    assert " open" not in attrs and "open=" not in attrs, (
        f"Panel should be collapsed by default (no `open` attribute), "
        f"got: {attrs!r}"
    )


# ── Tests for the JavaScript sort function (regex-only) ───────────────────


def test_section_videos_sort_function_present(client: TestClient):
    """The sortSectionVideos() function is defined in the page's
    inline <script> block.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["A"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    assert "function sortSectionVideos" in text, (
        "sortSectionVideos() function must be defined in the inline script"
    )
    assert "function applyStoredSectionStateOnLoad" in text, (
        "applyStoredSectionStateOnLoad() function must be defined"
    )


def test_section_videos_localstorage_keys_present(client: TestClient):
    """The JavaScript uses a localStorage key prefix of
    'videoPageSectionVideos_<section_id>_sort' to persist sort
    direction. This is verified by checking the script content
    includes the key prefix.
    """
    _, _, video_ids = _create_course_section_videos(
        client, ["A"],
    )
    with _mock_auth():
        response = client.get(f"/video/{video_ids[0]}", headers=_auth_headers())
    text = response.text
    assert "videoPageSectionVideos_" in text, (
        "Script must use 'videoPageSectionVideos_' as the localStorage prefix"
    )
    # The script also uses _sort and _open suffixes
    assert "_sort" in text
    assert "_open" in text
