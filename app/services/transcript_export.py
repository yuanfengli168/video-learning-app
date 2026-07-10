"""Transcript export — format a transcript dict as .md, .json, or .txt.

The three formats share one job: turn the in-memory transcript dict
(segments + language + duration) into a string the user can save.
We keep the formatting logic here (pure, no FastAPI / DB) so it's
trivial to unit-test and so the endpoint is a thin wrapper.

Output formats
--------------
- `.md` (Markdown):
      # {title}

      **Duration:** {dur}s | **Language:** {lang} | **Exported:** {date}

      ## Transcript

      [00:00:00] 第一段文字
      [00:00:03] 第二段文字
      ...
- `.json` (raw): the same JSON the transcript is stored as in the
  DB. Round-trip safe: `export_md(...) → import back` gives you the
  same dict.
- `.txt` (plain text): one segment per line, `[HH:MM:SS] {text}\n`.
  No markdown, no JSON, just text. Useful for grep, Word paste,
  reading on a phone.

Public API
----------
- `format_transcript(transcript: dict, fmt: str, *, video_title=None, exported_at=None) -> str`
- `export_extension(fmt: str) -> str`  — the file extension for a format
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

ExportFormat = Literal["md", "json", "txt"]
VALID_FORMATS: tuple[ExportFormat, ...] = ("md", "json", "txt")


def export_extension(fmt: str) -> str:
    """Return the file extension (without the dot) for a format.

    Used by the endpoint to set `Content-Disposition: attachment;
    filename="title.md"` so the browser saves the file with the right
    name.
    """
    if fmt == "md":
        return "md"
    if fmt == "json":
        return "json"
    if fmt == "txt":
        return "txt"
    raise ValueError(f"Unknown export format: {fmt!r}; expected one of {VALID_FORMATS}")


def format_transcript(
    transcript: dict[str, Any],
    fmt: str,
    *,
    video_title: str | None = None,
    exported_at: datetime | None = None,
) -> str:
    """Format a transcript dict as the requested format string.

    Args:
        transcript: the in-memory transcript dict (from json_to_transcript).
            Expected shape: {"segments": [{"start", "end", "text"}, ...],
                              "language": "zh", "duration": 170.5}.
        fmt: one of "md", "json", "txt".
        video_title: optional, used in the .md header. Defaults to
            "Transcript" if not provided.
        exported_at: optional datetime for the .md "Exported" field.
            Defaults to "now" in UTC.

    Raises:
        ValueError: if fmt is not one of the supported formats.

    Returns:
        The formatted string, ready to be sent as the response body.
    """
    if fmt == "md":
        return _format_md(transcript, video_title=video_title, exported_at=exported_at)
    if fmt == "json":
        return _format_json(transcript)
    if fmt == "txt":
        return _format_txt(transcript)
    raise ValueError(f"Unknown export format: {fmt!r}; expected one of {VALID_FORMATS}")


# ── Internal helpers ───────────────────────────────────────────────────────

def _format_md(
    transcript: dict[str, Any],
    *,
    video_title: str | None,
    exported_at: datetime | None,
) -> str:
    """Render the transcript as Markdown.

    The layout is intentionally minimal: title, one-line meta block,
    then one segment per line. This pastes cleanly into Obsidian,
    Notion, and blog posts without any cleanup.
    """
    title = video_title or "Transcript"
    duration = transcript.get("duration", 0)
    language = transcript.get("language", "unknown")
    when = (exported_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {title}",
        "",
        f"**Duration:** {duration:.1f}s | **Language:** {language} | **Exported:** {when}",
        "",
        "## Transcript",
        "",
    ]
    for seg in transcript.get("segments", []):
        start = _format_hms(seg.get("start", 0))
        text = seg.get("text", "").strip()
        lines.append(f"[{start}] {text}")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def _format_json(transcript: dict[str, Any]) -> str:
    """Serialize the transcript as JSON. Round-trip safe.

    We use `ensure_ascii=False` so Chinese text stays readable in
    the downloaded file (no \\uXXXX escape noise). `indent=2` makes
    the file human-editable.
    """
    return json.dumps(transcript, ensure_ascii=False, indent=2)


def _format_txt(transcript: dict[str, Any]) -> str:
    """Render the transcript as plain text.

    One segment per line, `[HH:MM:SS] {text}\n`. No markdown, no JSON.
    Note: HH not MM — durations over 1 hour are common (we have a
    1527s video from the user's test data).
    """
    lines: list[str] = []
    for seg in transcript.get("segments", []):
        start = _format_hms(seg.get("start", 0))
        text = seg.get("text", "").strip()
        lines.append(f"[{start}] {text}")
    return "\n".join(lines) + "\n"


def _format_hms(seconds: float) -> str:
    """Format seconds as HH:MM:SS.

    Note: this is the same as `format_timestamp` in transcription.py
    but kept here as a private helper to keep this module
    self-contained for testing. If format_timestamp ever changes, we
    can dedupe — for now, having two copies of a 4-line function is
    fine.
    """
    s = max(0, int(seconds))
    hours = s // 3600
    minutes = (s % 3600) // 60
    secs = s % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
