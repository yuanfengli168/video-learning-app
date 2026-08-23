"""YouTube Data API v3 client — fetch video metadata + caption listing.

Wraps the small subset of the YouTube API that MVP2 needs:

  - `videos.list?part=snippet,contentDetails&id=...` → title, channel,
    thumbnail URL, ISO 8601 duration
  - `captions.list?videoId=...` → list of available caption tracks
    (id, language, name, auto-generated flag)

We DO NOT download captions here — that requires OAuth credentials for
public videos OR yt-dlp fallback. Caption DOWNLOAD is Day 3; Day 2B
just lists what's available so the admin UI can show "this video has
English + Mandarin captions".

Quota (from Google):
  - videos.list: 1 unit per call
  - captions.list: 50 units per call
  - Daily free quota: 10,000 units
  - 100 admin adds/day → 5,100 units (well within free tier)

All errors are recoverable: if the API is unavailable, the admin
endpoint falls back to admin-provided title (Day 2A behavior). The
admin form already pre-fills the title; the API call is purely for
enrichment.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.services.youtube import extract_youtube_id

logger = logging.getLogger(__name__)


# YouTube Data API v3 base URL
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Request timeout — YouTube is fast, 10s is generous
DEFAULT_TIMEOUT_SECONDS = 10.0


# ─────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class CaptionTrack:
    """One caption track for a video."""
    id: str
    """Caption resource ID (used by captions.download, OAuth-required)."""
    language: str
    """BCP-47 language code (e.g. 'en', 'zh-Hans', 'ja')."""
    name: str
    """Human-readable label (often empty for auto-generated)."""
    auto_generated: bool
    """True if YouTube generated this caption (lower quality)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "language": self.language,
            "name": self.name,
            "auto_generated": self.auto_generated,
        }


@dataclass
class VideoMetadata:
    """Metadata for a YouTube video, fetched via the Data API."""
    youtube_id: str
    title: str
    channel: str
    thumbnail_url: str
    """Highest-resolution thumbnail available."""
    duration_seconds: int
    """Duration in seconds (parsed from ISO 8601, e.g. 'PT3M34S' → 214)."""
    caption_tracks: list[CaptionTrack] = field(default_factory=list)
    """Available caption tracks (language + auto-gen flag)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "youtube_id": self.youtube_id,
            "title": self.title,
            "channel": self.channel,
            "thumbnail_url": self.thumbnail_url,
            "duration_seconds": self.duration_seconds,
            "caption_tracks": [c.to_dict() for c in self.caption_tracks],
        }


# ─────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────


class YouTubeAPIError(Exception):
    """Base error for YouTube API failures."""


class YouTubeAPIKeyMissing(YouTubeAPIError):
    """Raised when settings.youtube_api_key is empty."""


class YouTubeVideoNotFound(YouTubeAPIError):
    """Raised when the video ID doesn't exist (404 from API or empty items)."""


class YouTubeQuotaExceeded(YouTubeAPIError):
    """Raised when API returns 403 with quotaExceeded reason."""


# ─────────────────────────────────────────────────────────────────────────
# ISO 8601 duration parser
# ─────────────────────────────────────────────────────────────────────────


# PT3M34S → 214, PT1H2M3S → 3723, PT45S → 45, PT12M → 720
_ISO_DURATION_RE = re.compile(
    r"^PT"
    r"(?:(\d+)H)?"
    r"(?:(\d+)M)?"
    r"(?:(\d+)S)?$"
)


def parse_iso8601_duration(duration: str) -> int:
    """Parse YouTube's ISO 8601 duration string into total seconds.

    Examples:
      "PT3M34S"  → 214
      "PT1H2M3S" → 3723
      "PT45S"    → 45
      "PT12M"    → 720
      "PT1H"     → 3600
      ""         → 0  (defensive: empty string)
      "invalid"  → 0  (defensive: returns 0 for malformed input)

    The YouTube API returns duration in this format regardless of locale.
    """
    if not duration:
        return 0
    m = _ISO_DURATION_RE.match(duration)
    if not m:
        logger.warning(f"Could not parse ISO 8601 duration: {duration!r}")
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


# ─────────────────────────────────────────────────────────────────────────
# Thumbnail picker
# ─────────────────────────────────────────────────────────────────────────


def pick_best_thumbnail(thumbnails: dict[str, dict]) -> str:
    """Pick the highest-resolution thumbnail from YouTube's options.

    YouTube returns up to 5 thumbnail sizes: default, medium, high,
    standard, maxres. We prefer maxres → standard → high → medium → default.
    Returns empty string if no thumbnails.
    """
    if not thumbnails:
        return ""
    for size in ("maxres", "standard", "high", "medium", "default"):
        if size in thumbnails and thumbnails[size].get("url"):
            return thumbnails[size]["url"]
    return ""


# ─────────────────────────────────────────────────────────────────────────
# Main client
# ─────────────────────────────────────────────────────────────────────────


class YouTubeAPIClient:
    """Thin synchronous client for YouTube Data API v3.

    Usage:
        client = YouTubeAPIClient()
        meta = client.get_video_metadata("dQw4w9WgXcQ")
        # meta.title, meta.duration_seconds, meta.caption_tracks, ...

    Args:
        api_key: Override the API key (default: settings.youtube_api_key).
        timeout: HTTP timeout in seconds.

    Raises:
        YouTubeAPIKeyMissing: If api_key is empty.
        YouTubeVideoNotFound: If the video doesn't exist.
        YouTubeQuotaExceeded: If daily quota is exhausted.
        YouTubeAPIError: For other API errors.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.youtube_api_key
        if not self.api_key:
            raise YouTubeAPIKeyMissing(
                "YOUTUBE_API_KEY not set. Get one from "
                "https://console.cloud.google.com → APIs & Services → Credentials"
            )
        self.timeout = timeout

    def get_video_metadata(self, video_id_or_url: str) -> VideoMetadata:
        """Fetch full metadata for a YouTube video.

        Combines two API calls (videos.list + captions.list). If the
        captions call fails (e.g. video owner disabled captions.list for
        this video), we return metadata with empty caption_tracks rather
        than failing the whole call — captions are a nice-to-have, not
        critical.
        """
        video_id = extract_youtube_id(video_id_or_url)
        if not video_id:
            raise YouTubeAPIError(f"Could not extract video ID from {video_id_or_url!r}")

        snippet = self._videos_list(video_id)
        if not snippet:
            raise YouTubeVideoNotFound(f"Video {video_id!r} not found (or is private)")

        title = snippet.get("title", "")
        channel = snippet.get("channelTitle", "")
        thumbnail_url = pick_best_thumbnail(snippet.get("thumbnails", {}))
        duration_iso = snippet.get("duration", "")
        duration_seconds = parse_iso8601_duration(duration_iso)

        # Captions are best-effort — don't fail the whole call
        try:
            captions = self.list_caption_tracks(video_id)
        except YouTubeAPIError as e:
            logger.info(f"Captions unavailable for {video_id}: {e}")
            captions = []

        return VideoMetadata(
            youtube_id=video_id,
            title=title,
            channel=channel,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration_seconds,
            caption_tracks=captions,
        )

    def _videos_list(self, video_id: str) -> dict | None:
        """Call videos.list?part=snippet,contentDetails and return the first item.

        Returns None if the video doesn't exist.
        """
        params = {
            "part": "snippet,contentDetails",
            "id": video_id,
            "key": self.api_key,
        }
        url = f"{YOUTUBE_API_BASE}/videos"
        try:
            resp = httpx.get(url, params=params, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise YouTubeAPIError(f"HTTP error calling YouTube API: {e}") from e
        return self._parse_videos_response(resp)

    def _parse_videos_response(self, resp: httpx.Response) -> dict | None:
        """Parse a videos.list response. Raises specific errors by status code."""
        if resp.status_code == 400:
            # Bad video ID, etc.
            raise YouTubeAPIError(f"YouTube API 400: {resp.text[:200]}")
        if resp.status_code == 403:
            # Quota exceeded OR API key invalid OR referer blocked
            data = resp.json()
            reason = ""
            try:
                reason = data["error"]["errors"][0].get("reason", "")
            except (KeyError, IndexError, TypeError):
                pass
            if "quota" in reason.lower():
                raise YouTubeQuotaExceeded(
                    f"YouTube API quota exceeded: {resp.text[:200]}"
                )
            raise YouTubeAPIError(
                f"YouTube API 403 (reason={reason!r}): {resp.text[:200]}"
            )
        if resp.status_code == 404:
            raise YouTubeVideoNotFound(f"YouTube API 404: {resp.text[:200]}")
        if resp.status_code != 200:
            raise YouTubeAPIError(
                f"YouTube API {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None
        # Combine snippet + contentDetails into one dict for caller convenience
        item = items[0]
        merged = {**item.get("snippet", {}), **item.get("contentDetails", {})}
        return merged

    def list_caption_tracks(self, video_id: str) -> list[CaptionTrack]:
        """Call captions.list?videoId=... and parse the tracks.

        Note: captions.list works with an API key for public videos, but
        captions.DOWNLOAD requires OAuth credentials. We just list — Day 3
        will add yt-dlp as a download fallback.

        Raises YouTubeAPIError on failure (caller decides whether to swallow).
        """
        params = {
            "part": "snippet",
            "videoId": video_id,
            "key": self.api_key,
        }
        url = f"{YOUTUBE_API_BASE}/captions"
        try:
            resp = httpx.get(url, params=params, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise YouTubeAPIError(f"HTTP error calling YouTube API: {e}") from e

        # captions.list sometimes returns 403 for videos whose owner
        # disabled the endpoint — treat as "no captions available"
        if resp.status_code == 403:
            data = resp.json()
            reason = ""
            try:
                reason = data["error"]["errors"][0].get("reason", "")
            except (KeyError, IndexError, TypeError):
                pass
            # captionsDisabled and forbidden are not fatal
            if reason in ("captionsDisabled", "forbidden", "notFound"):
                logger.info(f"Captions disabled/forbidden for {video_id}")
                return []
            if "quota" in reason.lower():
                raise YouTubeQuotaExceeded(
                    f"YouTube API quota exceeded on captions.list"
                )
            raise YouTubeAPIError(
                f"YouTube API 403 on captions.list (reason={reason!r})"
            )

        if resp.status_code == 404:
            return []  # no captions exist
        if resp.status_code != 200:
            raise YouTubeAPIError(
                f"YouTube API {resp.status_code} on captions.list: {resp.text[:200]}"
            )

        data = resp.json()
        tracks: list[CaptionTrack] = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            tracks.append(
                CaptionTrack(
                    id=item.get("id", ""),
                    language=snippet.get("language", ""),
                    name=snippet.get("name", ""),
                    auto_generated=snippet.get("trackKind", "") == "ASR",
                )
            )
        return tracks
