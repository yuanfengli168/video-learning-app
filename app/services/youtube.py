"""YouTube helpers — URL parsing, ID extraction, embed URL building.

This module is PURE (no HTTP, no DB, no I/O) for the URL-parsing helpers.
YouTube Data API v3 integration (caption fetch, metadata) will be added
in a follow-up once the YOUTUBE_API_KEY is provided.

Why a separate module:
- URL parsing is reused by admin upload + manual paste-back flows
- Pure functions are trivially testable (no fixtures needed)
- Keeps the YouTube concerns out of routers/models

Reference: https://stackoverflow.com/q/3452546 (URL formats we accept)

Supported URL formats (extracted via regex):
  https://www.youtube.com/watch?v=VIDEO_ID
  https://youtube.com/watch?v=VIDEO_ID
  https://youtu.be/VIDEO_ID
  https://www.youtube.com/shorts/VIDEO_ID       (YouTube Shorts)
  https://www.youtube.com/embed/VIDEO_ID
  https://m.youtube.com/watch?v=VIDEO_ID        (mobile)
  https://www.youtube.com/watch?v=VIDEO_ID&t=42s  (with timestamp)
  VIDEO_ID                                       (bare 11-char ID)

Not supported (return None):
  https://www.youtube.com/channel/UC...           (channel, not video)
  https://www.youtube.com/playlist?list=...       (playlist, not video)
  https://example.com/whatever                    (other domains)
"""

import re


# YouTube video IDs are exactly 11 chars: a-z, A-Z, 0-9, hyphen, underscore
# Reference: https://stackoverflow.com/q/37282050
# Note: this regex is strict on the 11-char length to prevent false positives
# (e.g. "watch?v=foo" would match if we allowed shorter IDs).
# NB: no \b word boundary — \b treats '-' as non-word char (since
# \w = [A-Za-z0-9_] only), breaking all-hyphens IDs like '------------'.
# Length is the only constraint.
_VIDEO_ID_CHARS = r"[A-Za-z0-9_-]{11}"
_VIDEO_ID_RE = re.compile(_VIDEO_ID_CHARS)


# Pattern order matters: most-specific first, fallback to bare ID last.
# Each pattern captures group(1) as the video ID.

# 1. youtube.com/watch?v=ID  (with optional extra query params like &t=42s)
_WATCH_V_RE = re.compile(
    rf"youtube\.com/watch\?v=({_VIDEO_ID_CHARS})",
    re.IGNORECASE,
)

# 2. youtu.be/ID  (short URL)
_SHORT_RE = re.compile(
    rf"youtu\.be/({_VIDEO_ID_CHARS})",
    re.IGNORECASE,
)

# 3. youtube.com/shorts/ID  (YouTube Shorts)
_SHORTS_RE = re.compile(
    rf"youtube\.com/shorts/({_VIDEO_ID_CHARS})",
    re.IGNORECASE,
)

# 4. youtube.com/embed/ID  (embed iframe — what we use in the player)
_EMBED_RE = re.compile(
    rf"youtube\.com/embed/({_VIDEO_ID_CHARS})",
    re.IGNORECASE,
)


def extract_youtube_id(url_or_id: str | None) -> str | None:
    """Extract a YouTube video ID from any supported URL format, or bare ID.

    Args:
        url_or_id: Full YouTube URL (any supported format), or just the
                   11-character video ID.

    Returns:
        The 11-character video ID if found, else None.

    Examples:
        >>> extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> extract_youtube_id("https://youtu.be/dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> extract_youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> extract_youtube_id("dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> extract_youtube_id("not a url")
        None
        >>> extract_youtube_id(None)
        None
        >>> extract_youtube_id("")
        None
    """
    if not url_or_id:
        return None

    # Type guard: only strings can be YouTube URLs/IDs.
    # Without this, calling extract_youtube_id(123) crashes with
    # AttributeError. Returning None is better UX (admin form validation
    # catches it and shows a friendly error).
    if not isinstance(url_or_id, str):
        return None

    s = url_or_id.strip()
    if not s:
        return None

    # Try each URL pattern in order. Most-specific first.
    for pattern in (_WATCH_V_RE, _SHORTS_RE, _EMBED_RE, _SHORT_RE):
        m = pattern.search(s)
        if m:
            return m.group(1)

    # Bare ID (no URL): if the whole string is exactly an 11-char ID, accept.
    if _VIDEO_ID_RE.fullmatch(s):
        return s

    return None


def is_valid_youtube_id(video_id: str | None) -> bool:
    """True if `video_id` is a well-formed YouTube video ID.

    Convenience wrapper around the regex used by extract_youtube_id().
    Useful for validating YouTube IDs from external sources (API responses,
    admin UI input).
    """
    if not video_id:
        return False
    return bool(_VIDEO_ID_RE.fullmatch(video_id))


def build_embed_url(video_id: str, autoplay: bool = False) -> str:
    """Build a youtube-nocookie.com embed URL for an iframe.

    Why youtube-nocookie.com:
    - YouTube's privacy-enhanced mode (no cookies set until user clicks play)
    - The default youtube.com/embed/ sets cookies on page load (privacy concern)
    - Recommended for educational content where viewers may not consent to
      tracking just by reading

    Args:
        video_id: 11-char YouTube video ID
        autoplay: If True, add ?autoplay=1 (used when user clicks "play")

    Returns:
        Full embed URL string. Empty string if video_id is invalid.

    Examples:
        >>> build_embed_url("dQw4w9WgXcQ")
        'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ'
        >>> build_embed_url("dQw4w9WgXcQ", autoplay=True)
        'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1'
        >>> build_embed_url("not-an-id")
        ''
    """
    if not is_valid_youtube_id(video_id):
        return ""
    base = f"https://www.youtube-nocookie.com/embed/{video_id}"
    if autoplay:
        return f"{base}?autoplay=1"
    return base


def build_thumbnail_url(video_id: str, quality: str = "hqdefault") -> str:
    """Build a YouTube thumbnail URL.

    Quality options (YouTube-provided):
      - "default"     (120x90)
      - "mqdefault"   (320x180)
      - "hqdefault"   (480x360, default — best quality for cards)
      - "sddefault"   (640x480)
      - "maxresdefault" (1280x720, may not exist for all videos)

    Reference: https://stackoverflow.com/q/2068344

    Args:
        video_id: 11-char YouTube video ID
        quality: Thumbnail quality key

    Returns:
        Full thumbnail URL string. Empty string if video_id is invalid.
    """
    if not is_valid_youtube_id(video_id):
        return ""
    return f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"


def build_watch_url(video_id: str) -> str:
    """Build the standard youtube.com watch URL (for "open on YouTube" links)."""
    if not is_valid_youtube_id(video_id):
        return ""
    return f"https://www.youtube.com/watch?v={video_id}"
