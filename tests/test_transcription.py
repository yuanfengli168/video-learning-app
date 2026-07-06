"""Tests for transcription service."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.transcription import (
    AVAILABLE_MODELS,
    format_timestamp,
    get_model,
    json_to_transcript,
    transcript_to_json,
    transcribe_video,
)


def test_available_models():
    """AVAILABLE_MODELS should include common model names."""
    assert "base" in AVAILABLE_MODELS
    assert "small" in AVAILABLE_MODELS
    assert "medium" in AVAILABLE_MODELS


def test_get_model_invalid_name():
    """get_model should raise ValueError for unknown model."""
    with pytest.raises(ValueError, match="Unknown model"):
        get_model("nonexistent-model")


def test_get_model_caching():
    """get_model should cache loaded models."""
    with patch("app.services.transcription._model_cache", {}):
        with patch("faster_whisper.WhisperModel") as mock_cls:
            get_model("base")
            get_model("base")  # Should use cache
            assert mock_cls.call_count == 1


def test_transcribe_video_file_not_found():
    """transcribe_video should raise FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        transcribe_video("/nonexistent/path/video.mp4")


def test_transcribe_video_success(tmp_path):
    """transcribe_video should return segments, language, duration."""
    # Create a dummy file
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"fake video content")

    # Mock the model and transcription
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 2.5
    mock_segment.text = "  Hello world  "

    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.duration = 10.5

    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    with patch("app.services.transcription.get_model", return_value=mock_model):
        result = transcribe_video(str(video_file), "base")

    assert result["language"] == "en"
    assert result["duration"] == 10.5
    assert len(result["segments"]) == 1
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 2.5
    assert result["segments"][0]["text"] == "Hello world"


def test_transcribe_video_multiple_segments(tmp_path):
    """transcribe_video should handle multiple segments."""
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"fake")

    segments = []
    for i in range(3):
        seg = MagicMock()
        seg.start = float(i * 5)
        seg.end = float(i * 5 + 3)
        seg.text = f"  Segment {i}  "
        segments.append(seg)

    mock_info = MagicMock()
    mock_info.language = "zh"
    mock_info.duration = 15.0

    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), mock_info)

    with patch("app.services.transcription.get_model", return_value=mock_model):
        result = transcribe_video(str(video_file), "small")

    assert len(result["segments"]) == 3
    assert result["segments"][0]["text"] == "Segment 0"
    assert result["segments"][2]["text"] == "Segment 2"


def test_transcript_to_json():
    """transcript_to_json should serialize to JSON string."""
    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}], "language": "en"}
    result = transcript_to_json(transcript)
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["segments"][0]["text"] == "Hi"


def test_json_to_transcript():
    """json_to_transcript should deserialize JSON string."""
    json_str = '{"segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}], "language": "en"}'
    result = json_to_transcript(json_str)
    assert result["language"] == "en"
    assert result["segments"][0]["text"] == "Hi"


def test_transcript_roundtrip():
    """transcript_to_json and json_to_transcript should be inverses."""
    original = {
        "segments": [{"start": 1.5, "end": 3.0, "text": "Test"}],
        "language": "en",
        "duration": 10.0,
    }
    json_str = transcript_to_json(original)
    restored = json_to_transcript(json_str)
    assert restored == original


def test_format_timestamp():
    """format_timestamp should format seconds as HH:MM:SS."""
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(65) == "00:01:05"
    assert format_timestamp(3661) == "01:01:01"
    assert format_timestamp(7384) == "02:03:04"