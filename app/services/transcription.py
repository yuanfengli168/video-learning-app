"""Whisper transcription service using Faster-Whisper.

Supports model selection (base, small, medium) and auto-download.
Returns timestamped segments.
"""

import json
from pathlib import Path
from typing import Any

# Model cache to avoid reloading
_model_cache: dict[str, Any] = {}

# Available Whisper models
AVAILABLE_MODELS = ["base", "small", "medium", "large-v3", "tiny"]


def get_model(model_name: str = "base"):
    """Load a Faster-Whisper model (cached).

    Args:
        model_name: One of AVAILABLE_MODELS.

    Returns:
        A faster_whisper.WhisperModel instance.
    """
    if model_name not in AVAILABLE_MODELS:
        raise ValueError(
            f"Unknown model '{model_name}'. Available: {AVAILABLE_MODELS}"
        )

    if model_name not in _model_cache:
        from faster_whisper import WhisperModel

        # device="cpu" for broad compatibility; compute_type="int8" for speed
        _model_cache[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")

    return _model_cache[model_name]


def transcribe_video(
    video_path: str | Path,
    model_name: str = "base",
) -> dict[str, Any]:
    """Transcribe a video file and return timestamped segments.

    Args:
        video_path: Path to the video file.
        model_name: Whisper model to use (base, small, medium, etc.).

    Returns:
        Dict with:
            - segments: list of {start, end, text}
            - language: detected language
            - duration: total duration in seconds
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    model = get_model(model_name)

    segments_iter, info = model.transcribe(str(video_path), beam_size=5)

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    return {
        "segments": segments,
        "language": info.language,
        "duration": round(info.duration, 2),
    }


def transcript_to_json(transcript: dict[str, Any]) -> str:
    """Serialize a transcript dict to JSON string for DB storage."""
    return json.dumps(transcript, ensure_ascii=False)


def json_to_transcript(json_str: str) -> dict[str, Any]:
    """Deserialize a JSON transcript string back to dict."""
    return json.loads(json_str)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS for display."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"