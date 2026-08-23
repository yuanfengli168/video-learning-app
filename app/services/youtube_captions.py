"""YouTube caption downloader (Day 3 — replaces the Whisper path for free-tier).

Why this exists
---------------
Whisper transcription takes 5-15 minutes per hour of video. YouTube
already publishes captions (manual + auto-generated) for the vast
majority of videos. When captions exist, downloading them is ~1-3
seconds. When they don't, we fall back to Whisper (handled by the
admin router via existing _run_transcribe_job logic — out of scope
for this module).

Strategy
--------
  1. yt-dlp with --skip-download + --write-subs --write-auto-subs
     fetches the .vtt/.srt for the requested language.
  2. We try languages in this priority order:
       a. User-locked language (video.language, set via UI dropdown)
       b. First entry of caption_languages JSON (from Day 2B API)
       c. 'en' if not in the list (huge coverage on YouTube)
       d. First available track, regardless of language
  3. Parse VTT/SRT into the same [{start, end, text}] shape Whisper
     produces, so downstream code (summary generation, transcript
     viewer) doesn't care which path produced the transcript.

Cost + reliability
------------------
  - Free (no API quota)
  - Network bound (~1-3s for the API call + caption fetch)
  - yt-dlp is the standard tool for this — same as youtube-dl,
    actively maintained, used by NewPipe, yt-dlp GUI, etc.
  - YouTube occasionally rate-limits. We respect that by NOT
    parallelizing: each caption fetch is sequential.

Output
------
  - Returns a transcript dict shaped like Whisper's:
      {"segments": [{"start": 0.0, "end": 2.5, "text": "..."}],
       "language": "en",
       "duration": 213.5}
  - On failure, raises a typed exception (no partial / silent fallback).

Why a separate service file
---------------------------
Mirrors app/services/youtube_api.py (Day 2B) — one file = one
external system. Easy to mock in tests (just patch
yt_dlp.YoutubeDL), easy to swap implementations later (e.g.
when YouTube's timedtext API breaks again).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class YouTubeCaptionsError(Exception):
    """Base class for all YouTube-caption-download failures.

    Catch this to handle 'any caption problem' generically.
    """


class YouTubeCaptionsUnavailable(YouTubeCaptionsError):
    """Video has no captions in any language (manual OR auto-generated).

    Common causes:
      - Channel disabled captions entirely
      - Video is region-locked / age-restricted
      - Video was just uploaded (YouTube hasn't generated auto-captions yet)
    """


class YouTubeCaptionsFailed(YouTubeCaptionsError):
    """yt-dlp / network error during caption download.

    Distinct from "no captions available" — this means we tried but
    something went wrong (rate-limited, DNS failure, parse error).
    """


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CaptionResult:
    """What fetch_youtube_captions() returns on success.

    Same JSON shape as Whisper's output (see app/services/transcription.py),
    so downstream code (transcript viewer, summary generator) works on
    either path's output identically.
    """

    segments: list[dict[str, float | str]]
    """[{start: float seconds, end: float seconds, text: str}, ...]"""
    language: str
    """BCP-47 code of the chosen track (e.g. 'en', 'zh-Hans')."""
    source: str
    """'manual' | 'auto' | 'unknown' — tells UI where captions came from.
    Manual = human-written. Auto = YouTube's ASR. Unknown = defensive
    default when yt-dlp can't tell (rare)."""
    duration: float
    """Total video duration in seconds (last segment's end + slack).
    May differ from YouTube Data API value by ±1s; downstream
    code clamps/averages."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Asset.content (same shape Whisper uses)."""
        return {
            "segments": self.segments,
            "language": self.language,
            "source": self.source,
            "duration": self.duration,
        }

    def to_json(self) -> str:
        """Serialize for direct DB insertion."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# VTT / SRT parsing (we don't rely on yt-dlp's parsing — we re-parse to
# ensure the same exact shape regardless of which subtitle format yt-dlp
# picked, and so we can swap formats without breaking downstream code)
# ─────────────────────────────────────────────────────────────────────────────


_VTT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)\s*-->\s*(?P<end>\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)"
)


def _vtt_timestamp_to_seconds(ts: str) -> float:
    """Convert VTT/SRT timestamp to seconds.

    Accepts:
      '1:02:03.456' (VTT hour:min:sec.msec)
      '12:34.567'   (VTT min:sec.msec, < 1 hour)
      '00:01:02,500' (SRT hour:min:sec,millis — comma decimal)
    """
    ts = ts.replace(",", ".")  # SRT uses comma
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    else:
        h = "0"
        m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_vtt_to_segments(vtt_text: str) -> list[dict[str, float | str]]:
    """Parse WebVTT or SubRip text into Whisper-shaped segments.

    Output shape (matches app/services/transcription.py):
      [{"start": float, "end": float, "text": str}, ...]

    Filters out empty / metadata-only cues (e.g. WEBVTT header, NOTE blocks).
    """
    segments: list[dict[str, float | str]] = []
    # Split into blocks separated by blank lines
    blocks = re.split(r"\n\s*\n", vtt_text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # Skip non-cue blocks: NOTE, STYLE, REGION
        first_upper = lines[0].upper()
        if first_upper.startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            continue
        # The timestamp line is either the first line OR the second
        # (VTT allows a cue identifier on line 1).
        ts_line_idx = 0
        if "-->" not in lines[0] and len(lines) > 1 and "-->" in lines[1]:
            ts_line_idx = 1
        if "-->" not in lines[ts_line_idx]:
            continue
        m = _VTT_TIMESTAMP_RE.search(lines[ts_line_idx])
        if not m:
            continue
        start = _vtt_timestamp_to_seconds(m.group("start"))
        end = _vtt_timestamp_to_seconds(m.group("end"))
        # Text is everything after the timestamp line, joined with spaces
        text = " ".join(lines[ts_line_idx + 1 :]).strip()
        # YouTube auto-captions sometimes leave tag placeholders like <c>
        # or &nbsp; entities — strip the structural tags, keep the text.
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        segments.append({"start": start, "end": end, "text": text})
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Language selection
# ─────────────────────────────────────────────────────────────────────────────


def _pick_language_priority(
    *,
    user_locked: Optional[str],
    available_languages: list[str],
    preferred_languages: tuple[str, ...] = ("en", "en-US", "en-GB"),
) -> Optional[str]:
    """Pick the best caption language to download.

    Priority:
      1. user_locked (UI language dropdown — admin picked this)
      2. first available track (YouTube's caption_languages list from Day 2B)
      3. preferred_languages fallback (default: any flavor of English)
      4. None (caller will try 'best' = whatever yt-dlp picks first)

    Args:
        user_locked: BCP-47 code from video.language (may be None).
        available_languages: List of BCP-47 codes from YouTube Data API
            (stored on video.caption_languages as a JSON string).
        preferred_languages: Fallback preference list. Default tries
            English variants first because the admin UI is in English.

    Returns:
        BCP-47 code, or None to let yt-dlp pick.
    """
    if user_locked and user_locked in available_languages:
        return user_locked
    if available_languages:
        return available_languages[0]
    if user_locked is None and available_languages is None:
        # Defensive: bad input. Let yt-dlp pick whatever it has.
        return None
    for pref in preferred_languages:
        if pref in (available_languages or []):
            return pref
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point — fetches captions via yt-dlp
# ─────────────────────────────────────────────────────────────────────────────


def fetch_youtube_captions(
    *,
    youtube_id: str,
    available_languages: list[str] | str | None = None,
    user_locked_language: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> CaptionResult:
    """Fetch captions for a YouTube video and return parsed segments.

    Args:
        youtube_id: 11-char YouTube video ID (e.g. 'dQw4w9WgXcQ').
        available_languages: List of BCP-47 codes YouTube claims to have.
            May be a JSON string (from DB) or a list (from API). None = unknown.
        user_locked_language: If set, prefer this language (must be in
            available_languages). Comes from video.language.
        work_dir: Where to write the temporary .vtt file. If None, uses
            system temp dir (cleaned up on success).

    Returns:
        CaptionResult with segments, language, source, duration.

    Raises:
        YouTubeCaptionsUnavailable: No captions in any language.
        YouTubeCaptionsFailed: yt-dlp / network error.

    Security:
      - youtube_id is validated to 11 chars before any I/O
      - We don't shell out; yt-dlp is imported as a Python module
      - Temporary files are removed in a `finally` block
      - We never eval / execute the .vtt contents — only regex-parse
    """
    # Defense in depth: validate before subprocess (even though yt-dlp
    # is just Python, no shell, this catches programmer errors early).
    if not youtube_id or len(youtube_id) != 11:
        raise YouTubeCaptionsFailed(
            f"Invalid YouTube ID: {youtube_id!r} (must be 11 chars)"
        )

    # Normalize available_languages (DB stores JSON string; callers may
    # pass either list or string)
    if isinstance(available_languages, str):
        try:
            available_languages = json.loads(available_languages)
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse caption_languages JSON %r, treating as empty",
                available_languages,
            )
            available_languages = []
    available_languages = list(available_languages or [])

    chosen_lang = _pick_language_priority(
        user_locked=user_locked_language,
        available_languages=available_languages,
    )

    # yt-dlp setup. We import here (not at module top) so test environments
    # that don't have yt-dlp installed can still import this module —
    # the actual subprocess work only happens inside fetch_youtube_captions().
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:
        raise YouTubeCaptionsFailed(
            "yt-dlp is not installed. Run `pip install yt-dlp`."
        ) from exc

    # yt-dlp options — see https://github.com/yt-dlp/yt-dlp
    # Key flags:
    #   skip_download       — we only want the subtitle file, not the video
    #   writesubtitles      — fetch human-uploaded captions
    #   writeautomaticsub   — fall back to YouTube's ASR-generated captions
    #   subtitlesformat     — prefer vtt (more uniform than ttml/srv1)
    #   subtitleslangs      — explicit language list (None = yt-dlp picks first)
    #   quiet + no_warnings — silent, we log our own errors
    #
    # Note: yt-dlp uses underscore-free option names (writesubtitles,
    # writeautomaticsub) — the underscore variants I initially tried
    # (write_subs) silently no-op, which is why the v0.1 test missed
    # this. Caught during live smoke test 2026-08-23.
    ydl_opts: dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt/best",
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
    }
    if chosen_lang is not None:
        # yt-dlp accepts a list, comma-separated string, or specific
        # marker like ".*" to match all variants. We pass ONLY the
        # chosen language (not ".*") — the wildcard pattern was tried
        # initially but it matches ALL ~100 languages YouTube lists
        # (including obscure ones like 'ab' Abkhaz), which trips
        # YouTube's rate limiter (HTTP 429 on the 2nd+ language
        # fetch). See live test 2026-08-23.
        #
        # Tradeoff: if our chosen_lang is wrong (e.g. user locked
        # "en-GB" but only "en" exists), yt-dlp will return empty and
        # the caller gets YouTubeCaptionsUnavailable. To handle that,
        # the worker retries WITHOUT subtitleslangs (let yt-dlp pick
        # the first available) — see _run_caption_download_job().
        ydl_opts["subtitleslangs"] = [chosen_lang]

    # yt-dlp writes its output files to the current working directory
    # by default. We redirect to a temp dir to avoid littering cwd.
    cleanup_work_dir = False
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="yt_captions_")
        cleanup_work_dir = True
    try:
        ydl_opts["outtmpl"] = str(Path(work_dir) / "%(id)s.%(ext)s")

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={youtube_id}",
                    download=True,
                )
        except Exception as exc:
            # yt-dlp's DownloadError / ExtractorError both inherit from
            # YoutubeDL's exception hierarchy. We collapse them to our
            # own exception so callers don't have to import yt-dlp types.
            err_msg = str(exc).lower()
            if (
                "private video" in err_msg
                or "video unavailable" in err_msg
                or "not found" in err_msg
            ):
                raise YouTubeCaptionsFailed(
                    f"YouTube video {youtube_id!r} is unavailable "
                    f"(private, deleted, or region-locked): {exc}"
                ) from exc
            raise YouTubeCaptionsFailed(
                f"yt-dlp failed for {youtube_id!r}: {exc}"
            ) from exc

        # Find the .vtt file yt-dlp wrote. It picks one of several
        # naming patterns depending on whether the track was manual
        # or auto-generated.
        #
        # Manual caption:     "<id>.<lang>.vtt"
        # Auto caption:       "<id>.<lang>.vtt" with a `.live_chat.json`
        #                     sibling OR no siblings at all
        # Multiple languages: yt-dlp picks the FIRST matching `subtitleslangs`
        #                     entry, which is `chosen_lang` if we set it,
        #                     else whatever's first in the manifest.
        #
        # We accept either:
        #   <work_dir>/<youtube_id>.<lang>.vtt      ← manual
        #   <work_dir>/<youtube_id>.vtt             ← when yt-dlp dropped the lang suffix
        # We then decide source='manual' vs 'auto' by checking if
        # the file was returned under the 'subtitles' (manual) or
        # 'automatic_captions' (auto) key in the manifest.
        vtt_path: Optional[Path] = None
        source: str = "unknown"
        chosen_lang_returned: str = chosen_lang or ""

        # Inspect the manifest to find the source type
        subs: dict[str, list[dict[str, Any]]] = info.get("subtitles", {}) or {}
        auto_subs: dict[str, list[dict[str, Any]]] = (
            info.get("automatic_captions", {}) or {}
        )

        # Prefer manual subs over auto when both exist
        # (manual = uploaded by video owner, usually higher quality)
        for lang in ([chosen_lang] if chosen_lang else list(subs.keys()) + list(auto_subs.keys())):
            if lang in subs:
                chosen_lang_returned = lang
                source = "manual"
                break
            if lang in auto_subs and source != "manual":
                chosen_lang_returned = lang
                source = "auto"

        # If subtitleslangs was set, yt-dlp wrote one file matching
        # that language. If not, it wrote multiple — pick the first.
        candidate_paths = sorted(Path(work_dir).glob(f"{youtube_id}*.vtt"))
        if not candidate_paths:
            raise YouTubeCaptionsUnavailable(
                f"YouTube returned no caption tracks for {youtube_id!r}. "
                f"Available languages: manual={list(subs.keys())}, "
                f"auto={list(auto_subs.keys())}. "
                f"Falling back to Whisper is recommended."
            )
        vtt_path = candidate_paths[0]

        # Determine source if still unknown (defensive)
        if source == "unknown":
            # Manual filenames: <id>.<lang>.vtt where lang matches one
            # of the `subtitles` keys. Auto filenames: same pattern but
            # lang matches one of the `automatic_captions` keys.
            for p in candidate_paths:
                # Extract lang from filename (basename minus .vtt)
                fname = p.stem  # "dQw4w9WgXcQ.en"
                parts = fname.split(".", 1)
                if len(parts) == 2:
                    lang_in_fname = parts[1]
                    if lang_in_fname in subs:
                        source = "manual"
                        chosen_lang_returned = lang_in_fname
                        break
                    if lang_in_fname in auto_subs:
                        source = "auto"
                        chosen_lang_returned = lang_in_fname

        vtt_text = vtt_path.read_text(encoding="utf-8")
        segments = _parse_vtt_to_segments(vtt_text)
        if not segments:
            raise YouTubeCaptionsUnavailable(
                f"Caption file for {youtube_id!r} parsed to 0 segments. "
                f"File may be malformed or contain only metadata."
            )

        # Duration = last segment's end (YouTube's last cue usually
        # trails the video by 1-2s; we use it as a good-enough proxy).
        duration = float(segments[-1]["end"])

        return CaptionResult(
            segments=segments,
            language=chosen_lang_returned,
            source=source,
            duration=duration,
        )

    finally:
        # Always clean up the temp dir we created
        if cleanup_work_dir and work_dir and os.path.isdir(work_dir):
            try:
                import shutil

                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception as exc:
                logger.warning(
                    "Failed to clean up work dir %s: %s", work_dir, exc
                )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: parse a pre-existing .vtt string (for tests + batch imports)
# ─────────────────────────────────────────────────────────────────────────────


def parse_vtt_text(vtt_text: str) -> list[dict[str, float | str]]:
    """Public wrapper around the internal VTT parser.

    Exposed so tests can verify parser behavior without running yt-dlp.
    Same output shape as fetch_youtube_captions().segments.
    """
    return _parse_vtt_to_segments(vtt_text)