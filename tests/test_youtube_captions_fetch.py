"""
Integration tests for app/services/youtube_captions.py — fetch_youtube_captions().

These tests cover the yt-dlp code path (lines 288-488 of youtube_captions.py)
that pure-function tests can't reach. We mock yt_dlp.YoutubeDL so tests are
fast and offline, but exercise the real:
  - Input validation (youtube_id length)
  - available_languages JSON-string normalization
  - Language priority selection + ydl_opts assembly
  - VTT file discovery in the temp dir
  - Source detection (manual vs auto) from the manifest
  - Exception paths (private video, no captions, malformed)
  - Temp dir cleanup on success AND failure
  - parse_vtt_text() public wrapper

Why this is a separate file:
  - test_youtube_captions.py = pure functions, < 50ms total
  - test_youtube_captions_fetch.py = integration with mocks, ~200ms
  - test_youtube_captions_job.py = background worker integration

Day 7 buffer: this is the test file that takes youtube_captions.py
from 49% to ~85%+ coverage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.youtube_captions import (
    CaptionResult,
    YouTubeCaptionsFailed,
    YouTubeCaptionsUnavailable,
    fetch_youtube_captions,
    parse_vtt_text,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello and welcome

00:00:03.500 --> 00:00:06.000
to the channel
"""


def _make_ydl_mock(work_dir: Path, *, manifest: dict[str, Any] | None = None):
    """Build a mock YoutubeDL context manager that writes a .vtt file.

    Args:
        work_dir: Where to write the .vtt file. The mock will simulate
            yt-dlp's filename pattern "<id>.<lang>.vtt".
        manifest: Value returned by extract_info(). If None, uses a
            sensible default (English manual captions).
    """
    if manifest is None:
        manifest = {
            "id": "dQw4w9WgXcQ",
            "subtitles": {
                "en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}],
            },
            "automatic_captions": {},
        }

    def _write_vtt_then_return_manifest(*args, **kwargs):
        # Simulate yt-dlp writing <id>.<lang>.vtt
        lang = list(manifest["subtitles"].keys())[0] if manifest["subtitles"] else "en"
        vtt_path = work_dir / f"dQw4w9WgXcQ.{lang}.vtt"
        vtt_path.write_text(SAMPLE_VTT, encoding="utf-8")
        return manifest

    ydl_instance = MagicMock()
    ydl_instance.extract_info.side_effect = _write_vtt_then_return_manifest
    # `with YoutubeDL(opts) as ydl:` — support the context manager protocol
    ydl_instance.__enter__ = MagicMock(return_value=ydl_instance)
    ydl_instance.__exit__ = MagicMock(return_value=False)
    return ydl_instance


# ─── Happy path: manual captions ─────────────────────────────────────────


class TestFetchYoutubeCaptionsHappyPath:
    """Success cases — captions download, parse, and return."""

    def test_returns_caption_result_on_success(self, tmp_path: Path):
        """fetch_youtube_captions returns a typed CaptionResult with parsed segments."""
        mock_ydl = _make_ydl_mock(tmp_path)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["en"],
                work_dir=str(tmp_path),
            )
        assert isinstance(result, CaptionResult)
        assert len(result.segments) == 2
        assert result.segments[0]["text"] == "Hello and welcome"
        assert result.segments[1]["text"] == "to the channel"
        assert result.language == "en"
        assert result.source == "manual"

    def test_duration_is_last_segment_end(self, tmp_path: Path):
        """Duration proxy = last cue's end time (good-enough for our needs)."""
        mock_ydl = _make_ydl_mock(tmp_path)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["en"],
                work_dir=str(tmp_path),
            )
        assert result.duration == 6.0  # last cue ends at 6.000s

    def test_auto_captions_used_when_no_manual(self, tmp_path: Path):
        """If only automatic_captions has the language, source='auto'."""
        manifest = {
            "id": "dQw4w9WgXcQ",
            "subtitles": {},  # no manual
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.com/auto-en.vtt"}],
            },
        }
        mock_ydl = _make_ydl_mock(tmp_path, manifest=manifest)
        # Override: yt-dlp writes <id>.en.vtt (same naming for auto)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["en"],
                work_dir=str(tmp_path),
            )
        assert result.source == "auto"

    def test_passes_subtitleslangs_to_yt_dlp_when_chosen(self, tmp_path: Path):
        """When language is picked, we pass it as subtitleslangs to yt-dlp.

        This is the bug-fix from live test 2026-08-23 — passing ".*"
        matched 100 languages and tripped YouTube's rate limiter.
        """
        mock_ydl = _make_ydl_mock(tmp_path)
        captured_opts = {}

        def capture_opts(opts):
            captured_opts.update(opts)
            return mock_ydl

        with patch("yt_dlp.YoutubeDL", side_effect=capture_opts):
            fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["zh-Hans"],
                work_dir=str(tmp_path),
            )
        assert "subtitleslangs" in captured_opts
        assert captured_opts["subtitleslangs"] == ["zh-Hans"]
        # NEVER the wildcard
        assert captured_opts["subtitleslangs"] != [".*"]

    def test_omits_subtitleslangs_when_no_language_picked(self, tmp_path: Path):
        """When no language is picked, let yt-dlp choose (no subtitleslangs key).

        This is the fallback path when the worker retries without a chosen lang.
        """
        mock_ydl = _make_ydl_mock(tmp_path)
        captured_opts = {}

        def capture_opts(opts):
            captured_opts.update(opts)
            return mock_ydl

        with patch("yt_dlp.YoutubeDL", side_effect=capture_opts):
            fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=[],  # nothing available
                work_dir=str(tmp_path),
            )
        # Without a chosen language, we don't constrain yt-dlp
        assert "subtitleslangs" not in captured_opts


# ─── Input validation ────────────────────────────────────────────────────


class TestFetchYoutubeCaptionsValidation:
    """Defensive checks BEFORE any I/O."""

    def test_rejects_empty_youtube_id(self):
        """Empty string is rejected without touching yt-dlp."""
        with pytest.raises(YouTubeCaptionsFailed, match="Invalid YouTube ID"):
            fetch_youtube_captions(youtube_id="")

    def test_rejects_too_short_youtube_id(self):
        """10-char ID (one short of YouTube's 11-char format) is rejected."""
        with pytest.raises(YouTubeCaptionsFailed, match="Invalid YouTube ID"):
            fetch_youtube_captions(youtube_id="dQw4w9WgXc")

    def test_rejects_too_long_youtube_id(self):
        """12-char ID (one too long) is rejected."""
        with pytest.raises(YouTubeCaptionsFailed, match="Invalid YouTube ID"):
            fetch_youtube_captions(youtube_id="dQw4w9WgXcQQ")

    def test_validates_before_calling_yt_dlp(self):
        """Validation runs BEFORE any yt-dlp import — even without yt-dlp
        installed, an invalid ID should fail with our typed error, not
        an ImportError."""
        with patch("yt_dlp.YoutubeDL", side_effect=ImportError):
            with pytest.raises(YouTubeCaptionsFailed, match="Invalid YouTube ID"):
                fetch_youtube_captions(youtube_id="bad")


# ─── Exception paths ─────────────────────────────────────────────────────


class TestFetchYoutubeCaptionsExceptions:
    """Error cases — must raise typed exceptions (not silent fallback)."""

    def test_raises_unavailable_when_no_caption_files(self, tmp_path: Path):
        """yt-dlp returned no .vtt files → YouTubeCaptionsUnavailable.

        This is the signal for the caller to fall back to Whisper.
        """
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "dQw4w9WgXcQ",
            "subtitles": {},
            "automatic_captions": {},
        }
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(YouTubeCaptionsUnavailable, match="no caption tracks"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    available_languages=["en"],
                    work_dir=str(tmp_path),
                )

    def test_raises_unavailable_when_parsed_to_zero_segments(self, tmp_path: Path):
        """yt-dlp wrote a .vtt but it parsed to 0 segments (e.g. only metadata)."""
        # Set up mock that writes a header-only VTT
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "dQw4w9WgXcQ",
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {},
        }
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            # yt-dlp mock doesn't write a file in this setup; the
            # "no candidate_paths" branch is already covered above.
            # This test exercises the OTHER zero-segments branch.
            (tmp_path / "dQw4w9WgXcQ.en.vtt").write_text("WEBVTT\n")  # header only
            with pytest.raises(YouTubeCaptionsUnavailable, match="0 segments"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    available_languages=["en"],
                    work_dir=str(tmp_path),
                )

    def test_raises_failed_on_private_video(self, tmp_path: Path):
        """Private/deleted/region-locked video → YouTubeCaptionsFailed with context."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Private video")
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(YouTubeCaptionsFailed, match="unavailable"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    work_dir=str(tmp_path),
                )

    def test_raises_failed_on_video_unavailable(self, tmp_path: Path):
        """'Video unavailable' message → YouTubeCaptionsFailed."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Video unavailable")
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(YouTubeCaptionsFailed, match="unavailable"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    work_dir=str(tmp_path),
                )

    def test_raises_failed_on_network_error(self, tmp_path: Path):
        """Generic yt-dlp error → YouTubeCaptionsFailed with context."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Connection timed out")
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            with pytest.raises(YouTubeCaptionsFailed, match="yt-dlp failed"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    work_dir=str(tmp_path),
                )

    def test_raises_failed_when_yt_dlp_not_installed(self, tmp_path: Path):
        """No yt-dlp → YouTubeCaptionsFailed with install hint.

        Patches the `from yt_dlp import YoutubeDL` import statement in
        youtube_captions.py to raise ImportError.
        """
        # Invalidate any cached import; the `from yt_dlp import` inside
        # fetch_youtube_captions() will raise ImportError
        import sys

        original_yt_dlp = sys.modules.get("yt_dlp")
        sys.modules["yt_dlp"] = None  # `None` causes ImportError on `from yt_dlp import X`
        try:
            with pytest.raises(YouTubeCaptionsFailed, match="yt-dlp is not installed"):
                fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    work_dir=str(tmp_path),
                )
        finally:
            if original_yt_dlp is not None:
                sys.modules["yt_dlp"] = original_yt_dlp
            else:
                sys.modules.pop("yt_dlp", None)


# ─── Input normalization ─────────────────────────────────────────────────


class TestFetchYoutubeCaptionsNormalization:
    """available_languages can arrive as a list, JSON string, or None."""

    def test_accepts_json_string_available_languages(self, tmp_path: Path):
        """DB stores caption_languages as JSON string — must be parsed."""
        mock_ydl = _make_ydl_mock(tmp_path)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages='["en", "zh-Hans"]',  # JSON string
                work_dir=str(tmp_path),
            )
        assert result.language == "en"

    def test_accepts_list_available_languages(self, tmp_path: Path):
        """Fresh API response is a Python list — must also work."""
        mock_ydl = _make_ydl_mock(tmp_path)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["en"],
                work_dir=str(tmp_path),
            )
        assert result.language == "en"

    def test_invalid_json_string_falls_back_to_empty_list(self, tmp_path: Path):
        """Bad JSON in DB → warn + treat as empty (don't crash)."""
        mock_ydl = _make_ydl_mock(tmp_path)
        # With empty available_languages, no subtitleslangs is set,
        # so yt-dlp is free to pick. Mock returns success either way.
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            # Should NOT raise — defensive normalization
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages="this is not JSON",
                work_dir=str(tmp_path),
            )
        assert isinstance(result, CaptionResult)

    def test_none_available_languages_works(self, tmp_path: Path):
        """available_languages=None is allowed (yt-dlp picks)."""
        mock_ydl = _make_ydl_mock(tmp_path)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            result = fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=None,
                work_dir=str(tmp_path),
            )
        assert isinstance(result, CaptionResult)


# ─── Cleanup ─────────────────────────────────────────────────────────────


class TestFetchYoutubeCaptionsCleanup:
    """Temp dir lifecycle — never leave junk on disk."""

    def test_uses_caller_provided_work_dir(self, tmp_path: Path):
        """If work_dir is given, we use it (and DON'T delete it)."""
        caller_dir = tmp_path / "caller_owned"
        caller_dir.mkdir()
        mock_ydl = _make_ydl_mock(caller_dir)
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            fetch_youtube_captions(
                youtube_id="dQw4w9WgXcQ",
                available_languages=["en"],
                work_dir=str(caller_dir),
            )
        # Caller's dir must still exist (we didn't create it)
        assert caller_dir.is_dir()

    def test_creates_temp_work_dir_when_not_provided(self, tmp_path: Path):
        """If work_dir is None, we create a temp dir AND clean it up."""
        captured_dir = []

        # Use a pre-built real temp dir so we don't have to mock mkdtemp
        # (which is hard because the SUT imports tempfile.mkdtemp directly).
        # Instead: verify the path-prefix the SUT uses, then make sure
        # NO such dir is left behind.
        import tempfile as tf

        real_dir = tf.mkdtemp(prefix="yt_captions_test_", dir=str(tmp_path))
        captured_dir.append(real_dir)

        # Mock mkdtemp to return our pre-built dir (no recursion risk)
        def fake_mkdtemp(*args, **kwargs):
            return real_dir

        # Write the VTT file in our pre-built dir, then mock yt-dlp to find it
        (Path(real_dir) / "dQw4w9WgXcQ.en.vtt").write_text(SAMPLE_VTT, encoding="utf-8")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "dQw4w9WgXcQ",
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {},
        }
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("app.services.youtube_captions.tempfile.mkdtemp", side_effect=fake_mkdtemp):
            with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
                result = fetch_youtube_captions(
                    youtube_id="dQw4w9WgXcQ",
                    available_languages=["en"],
                    work_dir=None,
                )
        # Temp dir must have been cleaned up
        assert len(captured_dir) == 1
        assert not Path(captured_dir[0]).exists(), (
            f"Temp dir {captured_dir[0]} not cleaned up after success"
        )
        assert result.language == "en"

    def test_cleans_up_temp_dir_on_failure(self, tmp_path: Path):
        """If yt-dlp fails mid-flight, temp dir must STILL be cleaned up."""
        captured_dir = []

        import tempfile as tf

        real_dir = tf.mkdtemp(prefix="yt_captions_test_", dir=str(tmp_path))
        captured_dir.append(real_dir)

        def fake_mkdtemp(*args, **kwargs):
            return real_dir

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Network error")
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("app.services.youtube_captions.tempfile.mkdtemp", side_effect=fake_mkdtemp):
            with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
                with pytest.raises(YouTubeCaptionsFailed):
                    fetch_youtube_captions(
                        youtube_id="dQw4w9WgXcQ",
                        work_dir=None,
                    )
        assert not Path(captured_dir[0]).exists(), (
            f"Temp dir {captured_dir[0]} not cleaned up after FAILURE"
        )


# ─── Public helpers ──────────────────────────────────────────────────────


class TestParseVttTextPublicAPI:
    """parse_vtt_text is the public wrapper around the internal parser."""

    def test_returns_list_of_segments(self):
        """Returns list of {start, end, text} dicts (Whisper shape)."""
        segments = parse_vtt_text(SAMPLE_VTT)
        assert isinstance(segments, list)
        assert len(segments) == 2
        for seg in segments:
            assert "start" in seg
            assert "end" in seg
            assert "text" in seg

    def test_matches_internal_parser_output(self):
        """Public wrapper produces the same output as calling _parse_vtt_to_segments."""
        from app.services.youtube_captions import _parse_vtt_to_segments
        public = parse_vtt_text(SAMPLE_VTT)
        internal = _parse_vtt_to_segments(SAMPLE_VTT)
        assert public == internal


class TestCaptionResultSerialization:
    """CaptionResult.to_dict() and to_json() — used for DB insertion."""

    def test_to_dict_has_whisper_shape(self):
        """to_dict must produce a Whisper-compatible dict shape."""
        result = CaptionResult(
            segments=[{"start": 0.0, "end": 1.0, "text": "hi"}],
            language="en",
            source="manual",
            duration=1.0,
        )
        d = result.to_dict()
        assert d["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
        assert d["language"] == "en"
        assert d["source"] == "manual"
        assert d["duration"] == 1.0

    def test_to_json_round_trips(self):
        """to_json output must be valid JSON that round-trips."""
        result = CaptionResult(
            segments=[{"start": 0.0, "end": 1.0, "text": "hi 你好"}],
            language="zh-Hans",
            source="auto",
            duration=1.5,
        )
        j = result.to_json()
        parsed = json.loads(j)
        assert parsed["language"] == "zh-Hans"
        assert "你好" in parsed["segments"][0]["text"]
        # ensure_ascii=False preserves unicode chars
        assert "你好" in j  # not escaped to \uXXXX
