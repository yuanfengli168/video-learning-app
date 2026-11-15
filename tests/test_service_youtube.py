"""Tests for app/services/youtube.py — URL parsing + ID validation.

Covers all 6 supported URL formats + edge cases + helper builders.
Coverage target: 100% (pure-logic module, easy to test exhaustively).
"""

import pytest

from app.services.youtube import (
    build_embed_url,
    build_thumbnail_url,
    build_watch_url,
    extract_youtube_id,
    is_valid_youtube_id,
)


# ─────────────────────────────────────────────────────────────────────────
# extract_youtube_id — all supported URL formats
# ─────────────────────────────────────────────────────────────────────────


class TestExtractYoutubeIdStandardFormats:
    """The 5 URL formats users will actually paste."""

    def test_standard_watch_url(self):
        """https://www.youtube.com/watch?v=VIDEO_ID"""
        assert extract_youtube_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_watch_url_without_www(self):
        """https://youtube.com/watch?v=VIDEO_ID (no www)"""
        assert extract_youtube_id(
            "https://youtube.com/watch?v=dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_watch_url_http(self):
        """http:// (not https) — old links still work"""
        assert extract_youtube_id(
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_youtu_be_short_url(self):
        """https://youtu.be/VIDEO_ID (the most common share URL)"""
        assert extract_youtube_id(
            "https://youtu.be/dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_youtu_be_with_query_params(self):
        """youtu.be with trailing ?si=... (new share tracking)"""
        assert extract_youtube_id(
            "https://youtu.be/dQw4w9WgXcQ?si=abc123"
        ) == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        """YouTube Shorts URL"""
        assert extract_youtube_id(
            "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_embed_url(self):
        """Embed iframe URL (what the player uses)"""
        assert extract_youtube_id(
            "https://www.youtube.com/embed/dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_mobile_youtube_url(self):
        """m.youtube.com (mobile variant)"""
        assert extract_youtube_id(
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
        ) == "dQw4w9WgXcQ"

    def test_watch_url_with_timestamp(self):
        """watch?v=ID&t=42s (start at 42 seconds)"""
        assert extract_youtube_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"
        ) == "dQw4w9WgXcQ"

    def test_watch_url_with_extra_params(self):
        """watch?v=ID&list=PLAYLIST&index=N (mixed in playlist)"""
        assert extract_youtube_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx&index=5"
        ) == "dQw4w9WgXcQ"

    def test_watch_url_uppercase_host(self):
        """Case-insensitive on host, BUT preserves ID case.

        YouTube video IDs are case-sensitive — we MUST preserve the case
        from the URL (case-insensitive host matching, case-sensitive ID).
        """
        assert extract_youtube_id(
            "HTTPS://WWW.YOUTUBE.COM/WATCH?V=DQw4w9WgXcQ"
        ) == "DQw4w9WgXcQ"  # upper case preserved from URL


class TestExtractYoutubeIdBareId:
    """When user pastes just the 11-char ID (no URL)."""

    def test_bare_id(self):
        assert extract_youtube_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_bare_id_with_whitespace_trimmed(self):
        """Leading/trailing whitespace stripped."""
        assert extract_youtube_id("  dQw4w9WgXcQ  ") == "dQw4w9WgXcQ"

    def test_bare_id_too_short_returns_none(self):
        """10 chars is not a valid YouTube ID."""
        assert extract_youtube_id("dQw4w9WgXc") is None

    def test_bare_id_too_long_returns_none(self):
        """12 chars is not a valid YouTube ID."""
        assert extract_youtube_id("dQw4w9WgXcQQ") is None


class TestExtractYoutubeIdRejectsNonVideo:
    """Things that look URL-ish but aren't videos."""

    def test_channel_url_returns_none(self):
        """Channels use /channel/UC... not videos."""
        assert extract_youtube_id(
            "https://www.youtube.com/channel/UC1234567890"
        ) is None

    def test_playlist_url_returns_none(self):
        """Playlists use ?list=PL... (no v= param)."""
        assert extract_youtube_id(
            "https://www.youtube.com/playlist?list=PLxxx"
        ) is None

    def test_user_url_returns_none(self):
        """Users have /user/USERNAME not /watch?v=."""
        assert extract_youtube_id(
            "https://www.youtube.com/user/somebody"
        ) is None

    def test_other_domain_returns_none(self):
        """Not YouTube at all."""
        assert extract_youtube_id(
            "https://vimeo.com/123456"
        ) is None

    def test_garbage_returns_none(self):
        assert extract_youtube_id("not a url") is None
        assert extract_youtube_id("hello world") is None
        assert extract_youtube_id("12345") is None


class TestExtractYoutubeIdEmpty:
    """Edge cases around empty/None input."""

    def test_none(self):
        assert extract_youtube_id(None) is None

    def test_empty_string(self):
        assert extract_youtube_id("") is None

    def test_whitespace_only(self):
        """Strip → empty → None."""
        assert extract_youtube_id("   ") is None

    def test_non_string_returns_none(self):
        """Type safety: non-strings return None gracefully.

        Better to return None than crash — a bad admin form input
        shouldn't 500 the server.
        """
        assert extract_youtube_id(123) is None  # type: ignore[arg-type]
        assert extract_youtube_id([]) is None   # type: ignore[arg-type]
        assert extract_youtube_id({}) is None   # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────
# is_valid_youtube_id — validation
# ─────────────────────────────────────────────────────────────────────────


class TestIsValidYoutubeId:
    """True iff input is exactly 11 chars from [A-Za-z0-9_-]."""

    def test_valid_id(self):
        assert is_valid_youtube_id("dQw4w9WgXcQ") is True

    def test_valid_id_with_hyphen(self):
        """Hyphens are allowed (e.g. legacy IDs). Must be exactly 11 chars."""
        assert is_valid_youtube_id("a-b-cd-e-fg") is True  # 10 chars actually
        assert is_valid_youtube_id("ab-cd-efg-h") is True  # 11 chars

    def test_valid_id_with_underscore(self):
        """Underscores are allowed."""
        assert is_valid_youtube_id("a_b_cd_e_fg") is True
        assert is_valid_youtube_id("ab_cd_efg_h") is True

    def test_valid_id_mixed_case(self):
        """Case-insensitive — YouTube IDs are case-sensitive but any case is valid."""
        assert is_valid_youtube_id("DQW4W9WGXCQ") is True
        assert is_valid_youtube_id("dQw4w9WgXcQ") is True

    def test_too_short(self):
        assert is_valid_youtube_id("dQw4w9WgXc") is False  # 10 chars

    def test_too_long(self):
        assert is_valid_youtube_id("dQw4w9WgXcQQ") is False  # 12 chars

    def test_empty(self):
        assert is_valid_youtube_id("") is False

    def test_none(self):
        assert is_valid_youtube_id(None) is False

    def test_invalid_chars(self):
        """Special chars not in [A-Za-z0-9_-] → invalid."""
        assert is_valid_youtube_id("dQw4w9WgXc!") is False  # ! not allowed
        assert is_valid_youtube_id("dQw4w9WgXc/") is False  # / not allowed
        assert is_valid_youtube_id("dQw4w9WgXc ") is False  # space not allowed


# ─────────────────────────────────────────────────────────────────────────
# build_embed_url — embed URL builder
# ─────────────────────────────────────────────────────────────────────────


class TestBuildEmbedUrl:
    """Build youtube-nocookie.com URLs for the player iframe."""

    def test_basic_embed(self):
        """No autoplay."""
        assert (
            build_embed_url("dQw4w9WgXcQ")
            == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
        )

    def test_embed_with_autoplay(self):
        """autoplay=1 appended as query string."""
        assert (
            build_embed_url("dQw4w9WgXcQ", autoplay=True)
            == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1"
        )

    def test_embed_explicit_no_autoplay(self):
        """autoplay=False (explicit) same as default."""
        assert (
            build_embed_url("dQw4w9WgXcQ", autoplay=False)
            == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
        )

    def test_invalid_id_returns_empty(self):
        """Defensive: bad ID → empty string (template-friendly)."""
        assert build_embed_url("not-an-id") == ""
        assert build_embed_url("") == ""
        assert build_embed_url(None) == ""

    def test_embed_uses_nocookie_domain(self):
        """The whole point: youtube-nocookie.com, NOT youtube.com."""
        url = build_embed_url("dQw4w9WgXcQ")
        assert "youtube-nocookie.com" in url
        assert "youtube.com/embed" not in url  # not the regular domain


# ─────────────────────────────────────────────────────────────────────────
# build_thumbnail_url — for video cards in catalog
# ─────────────────────────────────────────────────────────────────────────


class TestBuildThumbnailUrl:
    """Build YouTube's CDN thumbnail URLs."""

    def test_default_quality_hqdefault(self):
        """Default quality is hqdefault (480x360, best for cards)."""
        assert (
            build_thumbnail_url("dQw4w9WgXcQ")
            == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        )

    def test_quality_mqdefault(self):
        assert (
            build_thumbnail_url("dQw4w9WgXcQ", "mqdefault")
            == "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
        )

    def test_quality_maxresdefault(self):
        assert (
            build_thumbnail_url("dQw4w9WgXcQ", "maxresdefault")
            == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
        )

    def test_invalid_id_returns_empty(self):
        """Bad ID → empty string."""
        assert build_thumbnail_url("bad") == ""
        assert build_thumbnail_url(None) == ""


# ─────────────────────────────────────────────────────────────────────────
# build_watch_url — for "open on YouTube" links
# ─────────────────────────────────────────────────────────────────────────


class TestBuildWatchUrl:
    """Build the standard youtube.com watch URL."""

    def test_basic_watch_url(self):
        assert (
            build_watch_url("dQw4w9WgXcQ")
            == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

    def test_invalid_id_returns_empty(self):
        assert build_watch_url("bad") == ""
        assert build_watch_url("") == ""
        assert build_watch_url(None) == ""


# ─────────────────────────────────────────────────────────────────────────
# Property tests: round-trip + invariants
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("video_id", [
    "dQw4w9WgXcQ",
    "abcDEF_-123",
    "_" * 11,  # all underscores (11 chars)
    "-" * 11,  # all hyphens (11 chars)
    "00000000000",   # all zeros
])
def test_round_trip_extract_validate_build(video_id):
    """For any valid ID: extract(bare_id) == id, is_valid(id) True,
    builders return non-empty strings."""
    assert extract_youtube_id(video_id) == video_id
    assert is_valid_youtube_id(video_id) is True
    assert build_embed_url(video_id) != ""
    assert build_thumbnail_url(video_id) != ""
    assert build_watch_url(video_id) != ""


@pytest.mark.parametrize("video_id", [
    "dQw4w9WgXcQ",
    "abc123XYZ_-",
])
def test_extract_from_watch_url_round_trip(video_id):
    """Extracting from a watch URL returns the bare ID."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    assert extract_youtube_id(url) == video_id


@pytest.mark.parametrize("video_id", [
    "dQw4w9WgXcQ",
    "abc123XYZ_-",
])
def test_extract_from_short_url_round_trip(video_id):
    """Extracting from youtu.be/ID returns the bare ID."""
    url = f"https://youtu.be/{video_id}"
    assert extract_youtube_id(url) == video_id


def test_embed_url_pattern_matches_youtube_iframe_api():
    """The embed URL pattern matches what YouTube's IFrame Player API expects.

    Reference: https://developers.google.com/youtube/iframe_api_reference
    """
    url = build_embed_url("dQw4w9WgXcQ")
    # YouTube's IFrame API expects either:
    #   https://www.youtube.com/embed/VIDEO_ID
    #   https://www.youtube-nocookie.com/embed/VIDEO_ID
    # Both work; we use nocookie for privacy.
    assert "/embed/" in url
    assert url.endswith("/dQw4w9WgXcQ")
