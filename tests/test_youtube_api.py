"""Tests for the YouTube Data API v3 client (app/services/youtube_api.py).

Strategy: patch `httpx.get` to return canned responses. This avoids
network calls and quota usage. The live API is verified manually
(see doc/mvp2-final-go-live-plan.md Day 2B notes).

Coverage targets:
- parse_iso8601_duration: 100% (pure function)
- pick_best_thumbnail: 100% (pure function)
- YouTubeAPIClient.get_video_metadata: 95%+
- YouTubeAPIClient.list_caption_tracks: 95%+
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.youtube_api import (
    CaptionTrack,
    VideoMetadata,
    YouTubeAPIClient,
    YouTubeAPIError,
    YouTubeAPIKeyMissing,
    YouTubeQuotaExceeded,
    YouTubeVideoNotFound,
    parse_iso8601_duration,
    pick_best_thumbnail,
)


# ─────────────────────────────────────────────────────────────────────────
# parse_iso8601_duration
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("duration,expected", [
    ("PT3M34S", 214),       # minutes + seconds
    ("PT1H2M3S", 3723),     # hours + minutes + seconds
    ("PT45S", 45),          # seconds only
    ("PT12M", 720),         # minutes only
    ("PT1H", 3600),         # hours only
    ("PT0S", 0),            # zero
    ("", 0),                # empty defensive
    ("invalid", 0),         # malformed defensive
    ("PT", 0),              # bare PT
])
def test_parse_iso8601_duration(duration, expected):
    assert parse_iso8601_duration(duration) == expected


# ─────────────────────────────────────────────────────────────────────────
# pick_best_thumbnail
# ─────────────────────────────────────────────────────────────────────────


def test_pick_best_thumbnail_maxres():
    thumbs = {
        "default": {"url": "https://example.com/default.jpg"},
        "medium": {"url": "https://example.com/medium.jpg"},
        "high": {"url": "https://example.com/high.jpg"},
        "standard": {"url": "https://example.com/standard.jpg"},
        "maxres": {"url": "https://example.com/maxres.jpg"},
    }
    assert pick_best_thumbnail(thumbs) == "https://example.com/maxres.jpg"


def test_pick_best_thumbnail_only_small():
    thumbs = {"default": {"url": "https://example.com/default.jpg"}}
    assert pick_best_thumbnail(thumbs) == "https://example.com/default.jpg"


def test_pick_best_thumbnail_fallback_order():
    """Standard beats high if maxres missing."""
    thumbs = {
        "high": {"url": "https://example.com/high.jpg"},
        "standard": {"url": "https://example.com/standard.jpg"},
    }
    # standard should win (higher priority than high)
    assert pick_best_thumbnail(thumbs) == "https://example.com/standard.jpg"


def test_pick_best_thumbnail_empty():
    assert pick_best_thumbnail({}) == ""


def test_pick_best_thumbnail_missing_url():
    """If a size is listed but has no url, fall through."""
    thumbs = {
        "maxres": {},  # no url
        "high": {"url": "https://example.com/high.jpg"},
    }
    assert pick_best_thumbnail(thumbs) == "https://example.com/high.jpg"


def test_pick_best_thumbnail_url_is_empty_string():
    """If url field is empty string, fall through to next size."""
    thumbs = {
        "maxres": {"url": ""},  # empty string counts as missing
        "high": {"url": "https://example.com/high.jpg"},
    }
    assert pick_best_thumbnail(thumbs) == "https://example.com/high.jpg"


def test_pick_best_thumbnail_all_missing_urls():
    """All sizes listed but none have urls → empty string."""
    thumbs = {
        "maxres": {"url": ""},
        "high": {},
    }
    assert pick_best_thumbnail(thumbs) == ""


# ─────────────────────────────────────────────────────────────────────────
# CaptionTrack.to_dict + VideoMetadata.to_dict
# ─────────────────────────────────────────────────────────────────────────


def test_caption_track_to_dict():
    t = CaptionTrack(id="abc123", language="en", name="English", auto_generated=False)
    assert t.to_dict() == {
        "id": "abc123",
        "language": "en",
        "name": "English",
        "auto_generated": False,
    }


def test_video_metadata_to_dict():
    m = VideoMetadata(
        youtube_id="dQw4w9WgXcQ",
        title="Test Video",
        channel="Test Channel",
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        duration_seconds=214,
        caption_tracks=[
            CaptionTrack(id="cap1", language="en", name="", auto_generated=True),
        ],
    )
    d = m.to_dict()
    assert d["youtube_id"] == "dQw4w9WgXcQ"
    assert d["title"] == "Test Video"
    assert d["channel"] == "Test Channel"
    assert "maxresdefault" in d["thumbnail_url"]
    assert d["duration_seconds"] == 214
    assert len(d["caption_tracks"]) == 1
    assert d["caption_tracks"][0]["auto_generated"] is True


# ─────────────────────────────────────────────────────────────────────────
# YouTubeAPIClient — construction
# ─────────────────────────────────────────────────────────────────────────


def test_client_raises_without_api_key():
    with patch("app.services.youtube_api.settings.youtube_api_key", ""):
        with pytest.raises(YouTubeAPIKeyMissing):
            YouTubeAPIClient()


def test_client_with_explicit_api_key():
    client = YouTubeAPIClient(api_key="test-key")
    assert client.api_key == "test-key"


def test_client_with_settings_api_key():
    with patch("app.services.youtube_api.settings.youtube_api_key", "settings-key"):
        client = YouTubeAPIClient()
        assert client.api_key == "settings-key"


def test_client_custom_timeout():
    client = YouTubeAPIClient(api_key="test", timeout=5.0)
    assert client.timeout == 5.0


# ─────────────────────────────────────────────────────────────────────────
# YouTubeAPIClient.get_video_metadata — mocked HTTP
# ─────────────────────────────────────────────────────────────────────────


def _make_videos_response(
    title="Test Title",
    channel="Test Channel",
    duration="PT3M34S",
    thumbnails=None,
) -> dict:
    """Build a canned YouTube videos.list response."""
    if thumbnails is None:
        thumbnails = {
            "default": {"url": "https://example.com/default.jpg"},
            "high": {"url": "https://example.com/high.jpg"},
        }
    return {
        "items": [{
            "snippet": {
                "title": title,
                "channelTitle": channel,
                "thumbnails": thumbnails,
            },
            "contentDetails": {
                "duration": duration,
            },
        }],
    }


def _make_captions_response(tracks: list[dict] | None = None) -> dict:
    """Build a canned YouTube captions.list response."""
    if tracks is None:
        tracks = [
            {"id": "cap1", "snippet": {"language": "en", "name": "", "trackKind": ""}},
            {"id": "cap2", "snippet": {"language": "ja", "name": "Japanese", "trackKind": ""}},
            {"id": "cap3", "snippet": {"language": "zh", "name": "", "trackKind": "ASR"}},
        ]
    return {"items": tracks}


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)[:200]
    return resp


def test_get_metadata_happy_path():
    """Both API calls succeed → full VideoMetadata returned."""
    videos_data = _make_videos_response(
        title="Real Title",
        channel="Real Channel",
        duration="PT1H2M3S",
        thumbnails={
            "maxres": {"url": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"},
            "high": {"url": "https://i.ytimg.com/vi/abc/hqdefault.jpg"},
        },
    )
    captions_data = _make_captions_response()
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        # First call → videos.list, second call → captions.list
        mock_get.side_effect = [
            _mock_response(videos_data),
            _mock_response(captions_data),
        ]
        client = YouTubeAPIClient(api_key="test")
        meta = client.get_video_metadata("dQw4w9WgXcQ")

    assert meta.youtube_id == "dQw4w9WgXcQ"
    assert meta.title == "Real Title"
    assert meta.channel == "Real Channel"
    assert meta.duration_seconds == 3723  # 1h2m3s
    assert "maxresdefault" in meta.thumbnail_url  # maxres preferred
    assert len(meta.caption_tracks) == 3
    # ASR flagged as auto-generated
    assert meta.caption_tracks[2].auto_generated is True


def test_get_metadata_accepts_full_url():
    """Full URL is normalized to ID via extract_youtube_id."""
    videos_data = _make_videos_response()
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(videos_data),
            _mock_response(_make_captions_response()),
        ]
        client = YouTubeAPIClient(api_key="test")
        meta = client.get_video_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert meta.youtube_id == "dQw4w9WgXcQ"


def test_get_metadata_invalid_url_raises():
    client = YouTubeAPIClient(api_key="test")
    with pytest.raises(YouTubeAPIError, match="Could not extract"):
        client.get_video_metadata("not a youtube url at all")


def test_get_metadata_video_not_found():
    """API returns empty items → YouTubeVideoNotFound."""
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"items": []})
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeVideoNotFound):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_404_raises_not_found():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({}, status_code=404)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeVideoNotFound):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_400_raises_api_error():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"error": "bad"}, status_code=400)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="400"):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_403_malformed_error_response():
    """403 with non-standard error JSON → generic YouTubeAPIError (no crash)."""
    # Malformed: 'error' is a string, not a dict
    data = {"error": "forbidden"}
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(data, status_code=403)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="403"):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_quota_exceeded():
    """403 with quotaExceeded reason → YouTubeQuotaExceeded."""
    data = {
        "error": {
            "errors": [{"reason": "quotaExceeded"}],
        }
    }
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(data, status_code=403)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeQuotaExceeded):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_403_other_reason_raises_api_error():
    data = {"error": {"errors": [{"reason": "keyInvalid"}]}}
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(data, status_code=403)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="keyInvalid"):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_http_error_raises():
    """Network failure → YouTubeAPIError."""
    import httpx as real_httpx

    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.side_effect = real_httpx.ConnectError("network down")
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="HTTP error"):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_get_metadata_captions_failure_doesnt_fail_call():
    """If captions.list fails (e.g. captions disabled), still return metadata."""
    videos_data = _make_videos_response()
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        # First call succeeds, second (captions) returns 403 forbidden
        mock_get.side_effect = [
            _mock_response(videos_data),
            _mock_response(
                {"error": {"errors": [{"reason": "forbidden"}]}},
                status_code=403,
            ),
        ]
        client = YouTubeAPIClient(api_key="test")
        meta = client.get_video_metadata("dQw4w9WgXcQ")

    assert meta.title == "Test Title"
    assert meta.caption_tracks == []  # gracefully empty


def test_get_metadata_unexpected_status_code():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({}, status_code=500)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="500"):
            client.get_video_metadata("dQw4w9WgXcQ")


# ─────────────────────────────────────────────────────────────────────────
# YouTubeAPIClient.list_caption_tracks
# ─────────────────────────────────────────────────────────────────────────


def test_list_caption_tracks_empty():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"items": []})
        client = YouTubeAPIClient(api_key="test")
        tracks = client.list_caption_tracks("dQw4w9WgXcQ")
    assert tracks == []


def test_list_caption_tracks_403_forbidden_returns_empty():
    """captionsDisabled / forbidden → return [] (best-effort)."""
    for reason in ("captionsDisabled", "forbidden", "notFound"):
        with patch("app.services.youtube_api.httpx.get") as mock_get:
            mock_get.return_value = _mock_response(
                {"error": {"errors": [{"reason": reason}]}},
                status_code=403,
            )
            client = YouTubeAPIClient(api_key="test")
            assert client.list_caption_tracks("dQw4w9WgXcQ") == []


def test_list_caption_tracks_404_returns_empty():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({}, status_code=404)
        client = YouTubeAPIClient(api_key="test")
        assert client.list_caption_tracks("dQw4w9WgXcQ") == []


def test_list_caption_tracks_quota_exceeded():
    """403 quotaExceeded → YouTubeQuotaExceeded (NOT swallowed)."""
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(
            {"error": {"errors": [{"reason": "quotaExceeded"}]}},
            status_code=403,
        )
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeQuotaExceeded):
            client.list_caption_tracks("dQw4w9WgXcQ")


def test_list_caption_tracks_http_error_raises():
    import httpx as real_httpx

    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.side_effect = real_httpx.ReadTimeout("slow")
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="HTTP error"):
            client.list_caption_tracks("dQw4w9WgXcQ")


def test_list_caption_tracks_parses_asr_flag():
    """trackKind='ASR' → auto_generated=True; otherwise False."""
    tracks = [
        {"id": "cap1", "snippet": {"language": "en", "name": "English (manual)", "trackKind": "standard"}},
        {"id": "cap2", "snippet": {"language": "en", "name": "English (auto)", "trackKind": "ASR"}},
    ]
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"items": tracks})
        client = YouTubeAPIClient(api_key="test")
        result = client.list_caption_tracks("dQw4w9WgXcQ")
    assert result[0].auto_generated is False
    assert result[1].auto_generated is True


def test_list_caption_tracks_unexpected_status():
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({}, status_code=500)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="500"):
            client.list_caption_tracks("dQw4w9WgXcQ")


# ─────────────────────────────────────────────────────────────────────────
# Defensive fallbacks (for 100% coverage)
# ─────────────────────────────────────────────────────────────────────────


def test_pick_best_thumbnail_url_is_empty_string():
    """If url field is empty string, fall through to next size."""
    thumbs = {
        "maxres": {"url": ""},  # empty string counts as missing
        "high": {"url": "https://example.com/high.jpg"},
    }
    assert pick_best_thumbnail(thumbs) == "https://example.com/high.jpg"


def test_pick_best_thumbnail_all_missing_urls():
    """All sizes listed but none have urls → empty string."""
    thumbs = {
        "maxres": {"url": ""},
        "high": {},
    }
    assert pick_best_thumbnail(thumbs) == ""


def test_get_metadata_403_malformed_error_response():
    """403 with non-standard error JSON (no 'errors' list) → generic APIError."""
    # Malformed: 'error' is a string, not a dict
    data = {"error": "forbidden"}
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(data, status_code=403)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="403"):
            client.get_video_metadata("dQw4w9WgXcQ")


def test_list_caption_tracks_403_malformed_error():
    """captions.list 403 with malformed error JSON → generic APIError (not swallowed)."""
    data = {"error": "forbidden"}  # not a dict, no 'errors' list
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(data, status_code=403)
        client = YouTubeAPIClient(api_key="test")
        with pytest.raises(YouTubeAPIError, match="403"):
            client.list_caption_tracks("dQw4w9WgXcQ")


def test_get_metadata_quota_exceeded_in_captions_doesnt_fail_call():
    """If captions.list raises quota error, get_video_metadata still succeeds.

    Captions are best-effort — even quota errors on captions.list shouldn't
    fail the whole call. We just return empty caption_tracks.
    """
    videos_data = _make_videos_response()
    with patch("app.services.youtube_api.httpx.get") as mock_get:
        mock_get.side_effect = [
            _mock_response(videos_data),
            _mock_response(
                {"error": {"errors": [{"reason": "quotaExceeded"}]}},
                status_code=403,
            ),
        ]
        client = YouTubeAPIClient(api_key="test")
        meta = client.get_video_metadata("dQw4w9WgXcQ")
    assert meta.title == "Test Title"
    assert meta.caption_tracks == []
