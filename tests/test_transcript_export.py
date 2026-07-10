"""Tests for app/services/transcript_export.py."""

import json
from datetime import datetime

from app.services.transcript_export import (
    VALID_FORMATS,
    export_extension,
    format_transcript,
)


# ── export_extension ───────────────────────────────────────────────────────

def test_export_extension_md():
    assert export_extension("md") == "md"


def test_export_extension_json():
    assert export_extension("json") == "json"


def test_export_extension_txt():
    assert export_extension("txt") == "txt"


def test_export_extension_unknown_raises():
    """Defensive: bad format raises ValueError, not silent."""
    import pytest
    with pytest.raises(ValueError, match="Unknown export format"):
        export_extension("pdf")


# ── format_transcript dispatch ─────────────────────────────────────────────

def test_format_transcript_unknown_raises():
    """The dispatcher rejects unknown formats at the top level too."""
    import pytest
    with pytest.raises(ValueError, match="Unknown export format"):
        format_transcript({"segments": []}, "docx")


def test_valid_formats_constant():
    """VALID_FORMATS contains exactly the three supported formats."""
    assert set(VALID_FORMATS) == {"md", "json", "txt"}


# ── .md format ─────────────────────────────────────────────────────────────

def test_format_md_basic():
    """A simple transcript renders with title, meta, and one segment per line."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "Hello world"},
            {"start": 3.0, "end": 7.0, "text": "第二段"},
        ],
        "language": "zh",
        "duration": 7.0,
    }
    out = format_transcript(
        transcript, "md",
        video_title="My Video",
        exported_at=datetime(2026, 7, 10, 12, 0, 0),
    )
    # Title
    assert "# My Video" in out
    # Meta line
    assert "**Duration:** 7.0s" in out
    assert "**Language:** zh" in out
    assert "**Exported:** 2026-07-10 12:00 UTC" in out
    # Transcript header
    assert "## Transcript" in out
    # Segments
    assert "[00:00:00] Hello world" in out
    assert "[00:00:03] 第二段" in out


def test_format_md_default_title():
    """If video_title is None, default to 'Transcript'."""
    out = format_transcript({"segments": []}, "md")
    assert "# Transcript" in out


def test_format_md_default_exported_at():
    """If exported_at is None, use 'now' (some timestamp is in the output)."""
    out = format_transcript({"segments": []}, "md")
    assert "**Exported:**" in out
    # Year must be a 4-digit year (sanity check on the date format)
    import re
    assert re.search(r"\*\*Exported:\*\* \d{4}-\d{2}-\d{2}", out)


def test_format_md_long_video_uses_hh():
    """Durations over 1 hour show as HH:MM:SS, not MM:SS.

    Real scenario: user's 1527s video is 25 minutes, fine — but the
    25-min boundary is where 00:25:00 vs 25:00 matters. We use HH
    consistently so the format is unambiguous for any duration."""
    transcript = {
        "segments": [{"start": 3661.0, "end": 3664.0, "text": "One hour in"}],  # 1h 1m 1s
        "language": "en",
        "duration": 3664.0,
    }
    out = format_transcript(transcript, "md", video_title="Long")
    assert "[01:01:01] One hour in" in out


def test_format_md_empty_segments():
    """Empty transcript still produces a valid markdown file."""
    out = format_transcript({"segments": [], "language": "en", "duration": 0}, "md",
                            video_title="Empty")
    assert "# Empty" in out
    assert "## Transcript" in out
    # No segment lines but the file is still well-formed
    lines = out.splitlines()
    assert any("## Transcript" in l for l in lines)


def test_format_md_strips_segment_text():
    """Leading/trailing whitespace in segment text is stripped.

    The "[00:00:00] spaced out" line in the output should NOT have a
    double-space inside the text (e.g. "spaced  out"). The newline
    AFTER the line is fine (it's the markdown line separator).
    """
    transcript = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "  spaced out  \n"}],
        "language": "en",
        "duration": 1.0,
    }
    out = format_transcript(transcript, "md", video_title="Whitespace")
    assert "[00:00:00] spaced out" in out
    # No internal double-space (the .strip() collapsed leading + trailing ws)
    assert "spaced  out" not in out
    assert "  spaced" not in out
    assert "out  " not in out


# ── .json format ───────────────────────────────────────────────────────────

def test_format_json_round_trip():
    """JSON output round-trips back to the same dict via json.loads."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "Hello"},
            {"start": 3.0, "end": 7.0, "text": "世界"},
        ],
        "language": "zh",
        "duration": 7.0,
    }
    out = format_transcript(transcript, "json")
    parsed = json.loads(out)
    assert parsed == transcript


def test_format_json_unicode_preserved():
    """Chinese text in the JSON is NOT escaped to \\uXXXX.

    Why: ensure_ascii=False. The file should be readable in any
    editor without needing to mentally decode escapes."""
    import json
    out = format_transcript(
        {"segments": [{"start": 0, "end": 1, "text": "你好世界"}], "language": "zh", "duration": 1.0},
        "json",
    )
    # Raw Chinese in the output
    assert "你好世界" in out
    # And NOT escaped
    assert "\\u4f60\\u597d" not in out


def test_format_json_indented():
    """JSON is indented for human readability."""
    out = format_transcript(
        {"segments": [], "language": "en", "duration": 0}, "json",
    )
    # Newlines mean indent=2 is in effect
    assert "\n" in out
    assert "  " in out  # 2-space indent


# ── .txt format ────────────────────────────────────────────────────────────

def test_format_txt_basic():
    """Plain text: one segment per line, [HH:MM:SS] prefix."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "First line"},
            {"start": 3.0, "end": 7.0, "text": "Second line"},
        ],
        "language": "en",
        "duration": 7.0,
    }
    out = format_transcript(transcript, "txt")
    lines = out.rstrip("\n").split("\n")
    assert lines == [
        "[00:00:00] First line",
        "[00:00:03] Second line",
    ]


def test_format_txt_no_markdown():
    """The .txt output has no markdown — just plain text.

    No `#`, no `**`, no `##`. This is the file you'd paste into Word
    or grep over."""
    out = format_transcript(
        {"segments": [{"start": 0, "end": 1, "text": "Hello"}], "language": "en", "duration": 1.0},
        "txt",
        video_title="Title",
    )
    assert "#" not in out
    assert "**" not in out
    assert "##" not in out
    # Just the segment line
    assert out.strip() == "[00:00:00] Hello"


def test_format_txt_empty_transcript():
    """Empty transcript produces an empty string (no headers)."""
    out = format_transcript({"segments": []}, "txt")
    assert out == "\n"  # just the trailing newline


def test_format_txt_chinese_passes_through():
    """Chinese text is preserved as-is in the .txt output."""
    out = format_transcript(
        {"segments": [{"start": 0, "end": 1, "text": "你好世界"}], "language": "zh", "duration": 1.0},
        "txt",
    )
    assert "[00:00:00] 你好世界" in out
