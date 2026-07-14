"""Tests for MVP3.0 #2: whisper model picker with smart picks + MLX backend.

User-facing concept:
  - The model dropdown now has 6 options (4 manual + 2 smart picks).
  - The user picks a *choice* key, not a raw model_id.
  - The choice is resolved to a (backend, model_id) pair.
  - If the user picks the MLX smart pick on a non-Apple-Silicon
    Mac, we auto-fall back to the faster-whisper smart pick.
  - The transcribe endpoint persists the resolved (backend,
    model_id, fallback_reason) on the video row.
  - The UI uses the same registry to render the optgroup dropdown.

These tests cover:
  1. The MODEL_REGISTRY shape (single source of truth).
  2. is_mlx_available() — platform check.
  3. get_model_entry() — lookup + ValueError on unknown.
  4. resolve_model_choice() — all 4 scenarios:
     - manual pick → same entry, no fallback
     - smart fast pick → same entry, no fallback
     - smart extremely-fast pick on Apple Silicon → same entry, no fallback
     - smart extremely-fast pick on non-Apple-Silicon → fallback
  5. get_default_model_choice() — picks the right default per platform.
  6. transcribe_with_backend() — faster-whisper path returns the
     expected shape + _meta; mlx-whisper path raises
     NotImplementedError with a clear message.
  7. /api/videos/models endpoint shape.
  8. POST /api/videos/{id}/transcribe accepts new choice keys,
     persists the resolved (backend, model_id, fallback_reason)
     on the video row, and rejects unknown choices.
  9. The optgroup dropdown renders all 6 options in 2 groups with
     the default selected.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch("app.auth.dependencies.verify_token", return_value=FAKE_USER)


def _create_course_and_section(client: TestClient):
    """Helper: create a course + section, return (course_id, section_id)."""
    with _mock_auth():
        course_resp = client.post(
            "/api/courses", json={"title": "Whisper picker tests"},
            headers=_auth_headers(),
        )
        course_id = course_resp.json()["course_id"]
        section_resp = client.post(
            f"/api/courses/{course_id}/sections",
            json={"title": "S1"},
            headers=_auth_headers(),
        )
        section_id = section_resp.json()["section_id"]
    return course_id, section_id


def _upload_video(client: TestClient, section_id: str) -> str:
    """Helper: upload a video, return its id."""
    with _mock_auth():
        upload_resp = client.post(
            f"/api/videos/upload/{section_id}",
            files={"file": ("lecture.mp4", io.BytesIO(b"x" * 100), "video/mp4")},
            headers=_auth_headers(),
        )
    return upload_resp.json()["video_id"]


def _get_session():
    """Lazy SessionLocal accessor for direct DB inspection."""
    from app.database import SessionLocal
    return SessionLocal


# ─────────────────────────────────────────────────────────────────────────────
# 1. MODEL_REGISTRY shape
# ─────────────────────────────────────────────────────────────────────────────


def test_registry_has_seven_choices():
    """The registry must have exactly 7 entries: 4 manual + 3 smart.

    Updated 2026-07-14 when we added 'local-large-turbo' as the
    third smart pick (replaces the distil-large-v3 default).
    """
    from app.services.transcription import MODEL_REGISTRY
    assert len(MODEL_REGISTRY) == 7, (
        f"Expected 7 registry entries, got {len(MODEL_REGISTRY)}: "
        f"{sorted(MODEL_REGISTRY.keys())}"
    )


def test_registry_has_expected_keys():
    """Specific keys must exist (4 manual + 3 smart).

    Updated 2026-07-14: added 'local-large-turbo' to the
    expected set. See doc/CHANGELOG.md [2.0.1] for rationale.
    """
    from app.services.transcription import MODEL_REGISTRY
    expected = {
        "tiny", "base", "small", "medium",
        "local-best-and-fast", "local-best-and-extremely-fast",
        "local-large-turbo",
    }
    assert set(MODEL_REGISTRY.keys()) == expected


def test_registry_entries_have_required_fields():
    """Every entry must have label, model_id, backend, requires_mlx, group."""
    from app.services.transcription import MODEL_REGISTRY
    required = {"label", "model_id", "backend", "requires_mlx", "group"}
    for key, entry in MODEL_REGISTRY.items():
        assert required.issubset(entry.keys()), (
            f"Entry '{key}' missing fields: {required - set(entry.keys())}"
        )
        # Spot-check field types / values
        assert isinstance(entry["label"], str) and entry["label"], (
            f"Entry '{key}' has empty label"
        )
        assert isinstance(entry["model_id"], str) and entry["model_id"]
        assert entry["backend"] in {"faster-whisper", "mlx-whisper"}, (
            f"Entry '{key}' has unknown backend: {entry['backend']!r}"
        )
        assert isinstance(entry["requires_mlx"], bool)
        assert entry["group"] in {"manual", "smart"}


def test_manual_group_has_four_choices():
    """The 'manual' group is the 4 original models."""
    from app.services.transcription import MODEL_REGISTRY
    manual = {k for k, v in MODEL_REGISTRY.items() if v["group"] == "manual"}
    assert manual == {"tiny", "base", "small", "medium"}


def test_smart_group_has_three_choices():
    """The 'smart' group is the 3 picks (2 distil-large-v3 + 1 large-v3-turbo).

    Updated 2026-07-14: added 'local-large-turbo' (the new
    recommended default) to the smart group.
    """
    from app.services.transcription import MODEL_REGISTRY
    smart = {k for k, v in MODEL_REGISTRY.items() if v["group"] == "smart"}
    assert smart == {
        "local-best-and-fast",
        "local-best-and-extremely-fast",
        "local-large-turbo",
    }


def test_smart_extremely_fast_requires_mlx():
    """The 'extremely fast' smart pick must require MLX."""
    from app.services.transcription import MODEL_REGISTRY
    assert MODEL_REGISTRY["local-best-and-extremely-fast"]["requires_mlx"] is True
    assert MODEL_REGISTRY["local-best-and-fast"]["requires_mlx"] is False


def test_manual_choices_all_use_faster_whisper():
    """All 4 manual picks use the faster-whisper backend (no MLX)."""
    from app.services.transcription import MODEL_REGISTRY
    for key in ("tiny", "base", "small", "medium"):
        entry = MODEL_REGISTRY[key]
        assert entry["backend"] == "faster-whisper"
        assert entry["requires_mlx"] is False


def test_available_models_legacy_list_unchanged():
    """AVAILABLE_MODELS (the legacy flat list) still has just the 4 manual."""
    from app.services.transcription import AVAILABLE_MODELS
    assert AVAILABLE_MODELS == ["tiny", "base", "small", "medium"]


def test_smart_picks_exported_separately():
    """SMART_PICKS contains exactly the 3 smart pick keys.

    Updated 2026-07-14: 3 picks now (was 2). The new one is
    'local-large-turbo' (the recommended default).
    """
    from app.services.transcription import SMART_PICKS
    assert set(SMART_PICKS) == {
        "local-best-and-fast",
        "local-best-and-extremely-fast",
        "local-large-turbo",
    }


def test_all_model_choices_is_union():
    """ALL_MODEL_CHOICES is the union of manual + smart."""
    from app.services.transcription import (
        ALL_MODEL_CHOICES, AVAILABLE_MODELS, SMART_PICKS,
    )
    assert set(ALL_MODEL_CHOICES) == set(AVAILABLE_MODELS) | set(SMART_PICKS)


# ─────────────────────────────────────────────────────────────────────────────
# 2. is_mlx_available()
# ─────────────────────────────────────────────────────────────────────────────


def test_is_mlx_available_false_on_linux_x86(monkeypatch):
    """Linux/x86 is never Apple Silicon, so MLX is unavailable.

    This test runs on CI (Linux). For Apple Silicon detection, we
    mock platform.machine() to return 'arm64' AND the import to
    succeed (covered by the next test).
    """
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    from app.services.transcription import is_mlx_available
    assert is_mlx_available() is False


def test_is_mlx_available_false_on_arm64_without_mlx_whisper(monkeypatch):
    """Apple Silicon arm64, but mlx-whisper not installed → False.

    The first check (arch=arm64) passes, the second check (import)
    fails. We expect False without trying to actually install
    mlx-whisper in the test env.
    """
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    # Make sure the import fails
    import sys
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    from app.services.transcription import is_mlx_available
    assert is_mlx_available() is False


def test_is_mlx_available_true_on_arm64_with_mlx_whisper(monkeypatch):
    """Apple Silicon arm64 + mlx-whisper importable → True.

    Mocks the import to succeed by injecting a dummy module into
    sys.modules.
    """
    import sys
    import types
    dummy = types.ModuleType("mlx_whisper")
    monkeypatch.setitem(sys.modules, "mlx_whisper", dummy)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    from app.services.transcription import is_mlx_available
    assert is_mlx_available() is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_model_entry()
# ─────────────────────────────────────────────────────────────────────────────


def test_get_model_entry_returns_known_choice():
    """get_model_entry returns the full entry dict for a known key."""
    from app.services.transcription import get_model_entry
    entry = get_model_entry("base")
    assert entry["model_id"] == "base"
    assert entry["backend"] == "faster-whisper"
    assert entry["group"] == "manual"


def test_get_model_entry_raises_value_error_for_unknown():
    """get_model_entry raises ValueError with a helpful message for unknown keys."""
    from app.services.transcription import get_model_entry
    with pytest.raises(ValueError, match="Unknown model choice"):
        get_model_entry("not-a-real-choice")


def test_get_model_entry_message_lists_available():
    """The ValueError message lists the available choices."""
    from app.services.transcription import get_model_entry
    with pytest.raises(ValueError) as exc_info:
        get_model_entry("nope")
    msg = str(exc_info.value)
    # All 6 keys should appear in the error message
    for key in (
        "tiny", "base", "small", "medium",
        "local-best-and-fast", "local-best-and-extremely-fast",
    ):
        assert key in msg, f"ValueError should list '{key}' in available choices"


# ─────────────────────────────────────────────────────────────────────────────
# 4. resolve_model_choice()
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_manual_choice_returns_same_entry():
    """A manual choice resolves to its own entry (no fallback)."""
    from app.services.transcription import resolve_model_choice
    for key in ("tiny", "base", "small", "medium"):
        r = resolve_model_choice(key)
        assert r["fallback_occurred"] is False
        assert r["fallback_reason"] is None
        assert r["model_id"] == key
        assert r["backend"] == "faster-whisper"


def test_resolve_smart_fast_pick_returns_same_entry():
    """The 'local-best-and-fast' choice resolves to itself (no MLX needed)."""
    from app.services.transcription import resolve_model_choice
    r = resolve_model_choice("local-best-and-fast")
    assert r["fallback_occurred"] is False
    assert r["model_id"] == "distil-large-v3"
    assert r["backend"] == "faster-whisper"


def test_resolve_smart_extremely_fast_pick_no_mlx_falls_back(monkeypatch):
    """The 'extremely fast' pick falls back when MLX isn't available."""
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    from app.services.transcription import resolve_model_choice
    r = resolve_model_choice("local-best-and-extremely-fast")
    assert r["fallback_occurred"] is True
    assert r["backend"] == "faster-whisper"
    assert r["model_id"] == "distil-large-v3"
    # Fallback reason is a non-empty string that mentions MLX
    assert r["fallback_reason"] is not None
    assert "MLX" in r["fallback_reason"] or "Apple Silicon" in r["fallback_reason"]


def test_resolve_smart_extremely_fast_pick_with_mlx_does_not_fall_back(monkeypatch):
    """The 'extremely fast' pick with MLX available does NOT fall back."""
    import sys
    import types
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    from app.services.transcription import resolve_model_choice
    r = resolve_model_choice("local-best-and-extremely-fast")
    assert r["fallback_occurred"] is False
    assert r["fallback_reason"] is None
    assert r["backend"] == "mlx-whisper"
    assert r["model_id"] == "distil-large-v3"


def test_resolve_unknown_choice_raises():
    """Unknown choice raises ValueError (delegated to get_model_entry)."""
    from app.services.transcription import resolve_model_choice
    with pytest.raises(ValueError, match="Unknown model choice"):
        resolve_model_choice("nonsense")


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_default_model_choice()
# ─────────────────────────────────────────────────────────────────────────────


def test_default_is_local_large_turbo_when_mlx_available(monkeypatch):
    """When MLX is available, default = 'local-large-turbo' (mlx-community/whisper-large-v3-turbo).

    As of 2026-07-14, we switched the default away from the
    distil-large-v3 entries because that model is English-biased
    and ignores the language=zh lock. See app/services/transcription.py
    ::get_default_model_choice for the full rationale.
    """
    import sys
    import types
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    from app.services.transcription import get_default_model_choice
    assert get_default_model_choice() == "local-large-turbo"


def test_default_is_fast_smart_pick_on_intel(monkeypatch):
    """On Intel/x86 (no MLX), default = 'local-best-and-fast' (the faster-whisper one)."""
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    from app.services.transcription import get_default_model_choice
    assert get_default_model_choice() == "local-best-and-fast"


def test_default_is_fast_smart_pick_on_arm64_without_mlx(monkeypatch):
    """On arm64 without mlx-whisper installed, default = 'local-best-and-fast'."""
    import sys
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    from app.services.transcription import get_default_model_choice
    assert get_default_model_choice() == "local-best-and-fast"


# ─────────────────────────────────────────────────────────────────────────────
# 6. transcribe_with_backend() — faster-whisper path
# ─────────────────────────────────────────────────────────────────────────────


def test_transcribe_with_backend_faster_whisper_success(client: TestClient, tmp_path):
    """transcribe_with_backend with a manual choice (faster-whisper) returns
    the expected shape: segments + language + duration + _meta."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    class _FakeSegment:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class _FakeInfo:
        language = "en"
        duration = 5.0

    class _FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            return (
                iter([_FakeSegment(0.0, 2.5, "Hello")]),
                _FakeInfo(),
            )

    fake_file = tmp_path / "fake.mp4"
    fake_file.write_text("not a real video, but transcribe_with_backend only checks exists()")
    with patch("faster_whisper.WhisperModel", _FakeWhisperModel):
        from app.services.transcription import transcribe_with_backend
        result = transcribe_with_backend(str(fake_file), "base")

    # Standard transcript shape
    assert "segments" in result
    assert result["language"] == "en"
    assert result["duration"] == 5.0
    # _meta with the resolved choice
    assert "_meta" in result
    meta = result["_meta"]
    assert meta["choice"] == "base"
    assert meta["backend"] == "faster-whisper"
    assert meta["model_id"] == "base"
    assert meta["fallback_occurred"] is False
    assert meta["fallback_reason"] is None


def test_transcribe_with_backend_smart_fast_pick_uses_distil(monkeypatch, tmp_path):
    """The 'local-best-and-fast' choice uses distil-large-v3 via faster-whisper."""
    class _FakeSegment:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class _FakeInfo:
        language = "en"
        duration = 3.0

    class _FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            # Record the model_id that was passed in
            self.model_id = args[0] if args else kwargs.get("model_size_or_path")
        def transcribe(self, *args, **kwargs):
            return (
                iter([_FakeSegment(0.0, 1.0, "Hi")]),
                _FakeInfo(),
            )

    instances = []
    original_init = _FakeWhisperModel.__init__
    def recording_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.model_id_arg = args[0] if args else kwargs.get("model_size_or_path")
        instances.append(self.model_id_arg)

    _FakeWhisperModel.__init__ = recording_init

    with patch("faster_whisper.WhisperModel", _FakeWhisperModel):
        from app.services.transcription import transcribe_with_backend
        fake_file = tmp_path / "fake.mp4"
        fake_file.write_text("x")
        result = transcribe_with_backend(str(fake_file), "local-best-and-fast")

    # WhisperModel was constructed with "distil-large-v3"
    assert instances == ["distil-large-v3"], (
        f"Expected WhisperModel('distil-large-v3'), got {instances}"
    )
    assert result["_meta"]["model_id"] == "distil-large-v3"
    assert result["_meta"]["backend"] == "faster-whisper"


def test_transcribe_with_backend_mlx_path_calls_mlx_whisper(monkeypatch, tmp_path):
    """The mlx-whisper path actually calls mlx_whisper.transcribe with
    the anti-drift kwargs (condition_on_previous_text=False,
    compression_ratio_threshold=1.8).

    Pre-Part-A.2 this test asserted that the mlx-whisper path raised
    NotImplementedError (a placeholder for the follow-up commit).
    Part A.2 actually wires up the mlx call, so this test now
    verifies the new "mlx path dispatches correctly" behaviour.

    We mock mlx_whisper.transcribe to return a fake result, then
    assert:
      1. transcribe_with_backend() does NOT raise
      2. It calls mlx_whisper.transcribe with the model_id
      3. The kwargs include the anti-drift params
    """
    import sys
    import types

    # Mock mlx_whisper module + transcribe function
    fake_mlx = types.ModuleType("mlx_whisper")

    def fake_transcribe(path, **kwargs):
        return {
            "text": "fake transcription",
            "language": "zh",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "fake"},
            ],
        }

    fake_mlx.transcribe = fake_transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")

    # Spy on fake_transcribe to capture the kwargs it was called with
    called_kwargs = {}
    def spy_transcribe(path, **kwargs):
        called_kwargs.update(kwargs)
        return fake_transcribe(path, **kwargs)
    fake_mlx.transcribe = spy_transcribe

    from app.services.transcription import transcribe_with_backend
    tmp_file = tmp_path / "fake.mp4"
    tmp_file.write_text("x")
    result = transcribe_with_backend(str(tmp_file), "local-best-and-extremely-fast")

    # 1. Should NOT raise — and should return a result
    assert "segments" in result
    assert "language" in result
    # 2. Should have called mlx_whisper.transcribe with model_id
    assert called_kwargs.get("path_or_hf_repo") == "distil-large-v3"  # the pre-Part-A default for this choice
    # 3. Should include the anti-drift params
    assert called_kwargs.get("condition_on_previous_text") is False
    assert called_kwargs.get("compression_ratio_threshold") == 1.8


def test_transcribe_with_backend_mlx_path_passes_language(monkeypatch, tmp_path):
    """The mlx-whisper path passes the locked language to mlx_whisper.

    Part A #2b: when the user (or auto-detection) locks a language,
    transcribe_with_backend should pass `language=` and the matching
    `initial_prompt` to mlx_whisper.transcribe so the model is
    locked for the whole file.
    """
    import sys
    import types

    fake_mlx = types.ModuleType("mlx_whisper")
    called_kwargs = {}
    def spy(path, **kwargs):
        called_kwargs.update(kwargs)
        return {
            "text": "",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
        }
    fake_mlx.transcribe = spy
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")

    from app.services.transcription import transcribe_with_backend
    tmp_file = tmp_path / "fake.mp4"
    tmp_file.write_text("x")
    transcribe_with_backend(
        str(tmp_file),
        "local-best-and-extremely-fast",
        language="zh",
    )

    # Should have passed the locked language + initial_prompt
    assert called_kwargs.get("language") == "zh"
    assert called_kwargs.get("initial_prompt") == "以下是普通话的对话。"


def test_transcribe_with_backend_mlx_path_no_language_when_auto(monkeypatch, tmp_path):
    """The mlx-whisper path does NOT pass language/initial_prompt
    when the caller passes language=None (auto-detect / let whisper decide)."""
    import sys
    import types

    fake_mlx = types.ModuleType("mlx_whisper")
    called_kwargs = {}
    def spy(path, **kwargs):
        called_kwargs.update(kwargs)
        return {
            "text": "",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
        }
    fake_mlx.transcribe = spy
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")

    from app.services.transcription import transcribe_with_backend
    tmp_file = tmp_path / "fake.mp4"
    tmp_file.write_text("x")
    transcribe_with_backend(
        str(tmp_file),
        "local-best-and-extremely-fast",
        language=None,  # explicit auto
    )

    # Should NOT have language or initial_prompt in kwargs
    assert "language" not in called_kwargs
    assert "initial_prompt" not in called_kwargs


def test_transcribe_with_backend_mlx_path_falls_back_to_faster(monkeypatch, tmp_path):
    """On non-Apple-Silicon, 'extremely fast' auto-falls back to faster-whisper,
    so transcribe_with_backend uses the faster-whisper path (not the
    NotImplementedError)."""
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")

    class _FakeSegment:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t

    class _FakeInfo:
        language = "en"
        duration = 2.0

    class _FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            return (iter([_FakeSegment(0.0, 1.0, "x")]), _FakeInfo())

    with patch("faster_whisper.WhisperModel", _FakeWhisperModel):
        from app.services.transcription import transcribe_with_backend
        fake_file = tmp_path / "fake.mp4"
        fake_file.write_text("x")
        result = transcribe_with_backend(str(fake_file), "local-best-and-extremely-fast")

    # No NotImplementedError; ran on the faster-whisper path
    assert result["_meta"]["backend"] == "faster-whisper"
    assert result["_meta"]["fallback_occurred"] is True
    assert result["_meta"]["model_id"] == "distil-large-v3"


def test_transcribe_with_backend_file_not_found():
    """Non-existent file path raises FileNotFoundError."""
    from app.services.transcription import transcribe_with_backend
    with pytest.raises(FileNotFoundError):
        transcribe_with_backend("/this/does/not/exist.mp4", "base")


# ─────────────────────────────────────────────────────────────────────────────
# 7. /api/videos/models endpoint
# ─────────────────────────────────────────────────────────────────────────────


def test_models_endpoint_returns_new_shape(client: TestClient):
    """/api/videos/models returns {choices, default, models: legacy}."""
    with _mock_auth():
        resp = client.get("/api/videos/models", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data
    assert "default" in data
    assert "models" in data  # legacy field

    # Legacy 'models' field is the 4 manual ones
    assert set(data["models"]) == {"tiny", "base", "small", "medium"}

    # 'choices' has all 7 entries (4 manual + 3 smart) with the right shape
    assert len(data["choices"]) == 7
    for choice in data["choices"]:
        assert "key" in choice
        assert "label" in choice
        assert "group" in choice
        assert choice["group"] in {"manual", "smart"}

    # 'default' is a valid choice key
    assert data["default"] in {
        "tiny", "base", "small", "medium",
        "local-best-and-fast", "local-best-and-extremely-fast",
        "local-large-turbo",
    }


def test_models_endpoint_default_reflects_platform(client: TestClient, monkeypatch):
    """The 'default' field reflects the current platform's best option.

    We mock to make MLX unavailable (x86) and assert the default
    is the faster-whisper smart pick, not the MLX one. The opposite
    is covered by get_default_model_choice tests above.
    """
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    with _mock_auth():
        resp = client.get("/api/videos/models", headers=_auth_headers())
    data = resp.json()
    # On x86 with no MLX, default should be the fast (not extremely-fast) pick
    assert data["default"] == "local-best-and-fast"


# ─────────────────────────────────────────────────────────────────────────────
# 8. POST /api/videos/{id}/transcribe — accept new choices, persist resolved info
# ─────────────────────────────────────────────────────────────────────────────


def test_transcribe_endpoint_accepts_manual_choice(client: TestClient):
    """Manual pick ('base') is accepted and persisted correctly."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=base",
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    # Inspect the video row
    with _get_session()() as db:
        from app.models import Video
        v = db.get(Video, video_id)
        assert v.whisper_model == "base"
        assert v.whisper_backend == "faster-whisper"
        assert v.whisper_resolved_model == "base"
        # No fallback reason for a manual pick
        assert v.whisper_fallback_reason is None


def test_transcribe_endpoint_accepts_smart_fast_pick(client: TestClient):
    """Smart fast pick is accepted and persisted as distil-large-v3."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=local-best-and-fast",
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    with _get_session()() as db:
        from app.models import Video
        v = db.get(Video, video_id)
        # User's choice stored as-is
        assert v.whisper_model == "local-best-and-fast"
        # Resolved to faster-whisper + distil-large-v3
        assert v.whisper_backend == "faster-whisper"
        assert v.whisper_resolved_model == "distil-large-v3"
        assert v.whisper_fallback_reason is None


def test_transcribe_endpoint_smart_extremely_fast_falls_back_on_x86(
    client: TestClient, monkeypatch,
):
    """Smart MLX pick on x86 falls back to faster-whisper smart pick.

    The user picked 'local-best-and-extremely-fast' but on x86 the
    dispatcher falls back. The video row records:
      - whisper_model = the user's choice ('local-best-and-extremely-fast')
      - whisper_backend = the backend that actually runs ('faster-whisper')
      - whisper_resolved_model = the actual model ('distil-large-v3')
      - whisper_fallback_reason = a non-empty explanation
    """
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=local-best-and-extremely-fast",
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    with _get_session()() as db:
        from app.models import Video
        v = db.get(Video, video_id)
        # User's choice preserved
        assert v.whisper_model == "local-best-and-extremely-fast"
        # Fallback happened: actually ran on faster-whisper
        assert v.whisper_backend == "faster-whisper"
        assert v.whisper_resolved_model == "distil-large-v3"
        # Fallback reason recorded
        assert v.whisper_fallback_reason is not None
        assert len(v.whisper_fallback_reason) > 0


def test_transcribe_endpoint_smart_extremely_fast_no_fallback_on_m1(
    client: TestClient, monkeypatch,
):
    """Smart MLX pick on arm64+MLX does NOT fall back.

    Mocks MLX as available (arm64 + importable) and asserts the
    video row records the actual MLX backend, not a fallback.
    """
    import sys
    import types
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=local-best-and-extremely-fast",
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    with _get_session()() as db:
        from app.models import Video
        v = db.get(Video, video_id)
        assert v.whisper_model == "local-best-and-extremely-fast"
        assert v.whisper_backend == "mlx-whisper"
        assert v.whisper_resolved_model == "distil-large-v3"
        assert v.whisper_fallback_reason is None


def test_transcribe_endpoint_rejects_unknown_choice(client: TestClient):
    """Unknown choice key returns 400 with a list of valid choices."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.post(
            f"/api/videos/{video_id}/transcribe?model_name=not-a-real-model",
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # Error message lists the available choices
    for key in (
        "tiny", "base", "small", "medium",
        "local-best-and-fast", "local-best-and-extremely-fast",
    ):
        assert key in detail, f"Error should list '{key}': {detail}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Optgroup dropdown UI rendering
# ─────────────────────────────────────────────────────────────────────────────


def test_video_page_has_two_optgroups(client: TestClient):
    """The video page <select> must have exactly 2 <optgroup> children."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert resp.status_code == 200
    # Two <optgroup> tags
    assert resp.text.count("<optgroup") == 2, (
        f"Expected 2 <optgroup> tags, found {resp.text.count('<optgroup')}"
    )


def test_video_page_optgroup_manual_contains_four_originals(client: TestClient):
    """The 'Manual' optgroup contains tiny/base/small/medium."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    text = resp.text
    # All 4 manual options present
    for value in ("tiny", "base", "small", "medium"):
        assert f'value="{value}"' in text, f"Manual optgroup missing '{value}'"


def test_video_page_optgroup_smart_contains_two_picks(client: TestClient):
    """The 'Smart picks' optgroup contains the 2 new options."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    text = resp.text
    for value in ("local-best-and-fast", "local-best-and-extremely-fast"):
        assert f'value="{value}"' in text, f"Smart picks optgroup missing '{value}'"


def test_video_page_no_option_is_pre_selected(client: TestClient):
    """The model dropdown has no hard-coded 'selected' attribute.

    The default selection for the MODEL dropdown is set by JS on
    page load (which fetches /api/videos/models). The HTML should
    have no hard-coded 'selected' on any model <option> so the JS
    is the single source of truth.

    The LANGUAGE dropdown (added in MVP3.0 #2b) is allowed to
    have a server-side default based on `video.language` (so a
    new upload shows "Auto-detect" by default, and a re-loaded
    page shows the previously-detected language). We narrow the
    regex to only the #whisper-model <select> to avoid catching
    the legitimate language preselection.

    We grep for `<option ... selected` specifically to avoid false
    positives (Tailwind classes like `selected:bg-...` and Starlette
    internals like `_handle_selected` both contain the substring
    "selected" but don't have a literal ' selected' attribute on
    an option tag).
    """
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    text = resp.text
    # Extract just the model dropdown's <option> tags. The
    # #whisper-model select is followed by a #whisper-language
    # select, so we slice from the start of #whisper-model through
    # its closing </select>.
    import re
    m = re.search(
        r'<select[^>]*id="whisper-model".*?</select>',
        text,
        re.DOTALL,
    )
    assert m, "could not find #whisper-model <select> in the page"
    model_select = m.group(0)
    matches = re.findall(r"<option[^>]*\bselected\b", model_select)
    assert not matches, (
        f"No model <option> should be hard-coded 'selected' — "
        f"JS sets the default. Found: {matches}"
    )


def test_video_page_default_selector_js_present(client: TestClient):
    """The page must include the JS that fetches /api/videos/models and
    sets the dropdown to the server's recommended default."""
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    text = resp.text
    assert "/api/videos/models" in text, (
        "JS that fetches /api/videos/models is missing from the page"
    )
    assert "setDefaultWhisperModel" in text or "select.value" in text, (
        "JS that applies the default to the dropdown is missing"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 10. End-to-end: smart pick → resolved column → page rendering
# ─────────────────────────────────────────────────────────────────────────────


def test_smart_pick_end_to_end_visual_marker(client: TestClient):
    """After a smart pick is processed, the video page shows the resolved
    backend so the user can see what actually ran.

    Sanity test: it covers the 'row has backend info' -> 'page renders
    the backend in the status area' chain. We don't assert the
    exact UI (that's brittle); we just check that the backend label
    appears in the HTML somewhere when a smart pick was used.
    """
    course_id, section_id = _create_course_and_section(client)
    video_id = _upload_video(client, section_id)

    # Simulate the user picking the fast smart pick
    with _mock_auth():
        client.post(
            f"/api/videos/{video_id}/transcribe?model_name=local-best-and-fast",
            headers=_auth_headers(),
        )

    # Manually mark the video as ready (so the page shows the row)
    with _get_session()() as db:
        from app.models import Video
        v = db.get(Video, video_id)
        v.status = "ready"
        v.duration = 100.0
        # Pre-create a transcript asset so the page renders it
        from app.models import Asset
        from app.services.transcription import transcript_to_json
        db.add(Asset(
            id=f"t-{video_id[:8]}",
            video_id=video_id,
            asset_type="transcript",
            content=transcript_to_json({
                "segments": [{"start": 0, "end": 1, "text": "hi"}],
                "language": "en",
                "duration": 1.0,
            }),
        ))
        db.commit()

    # Now fetch the page
    with _mock_auth():
        resp = client.get(f"/video/{video_id}", headers=_auth_headers())
    assert resp.status_code == 200
    # The whisper_resolved_model "distil-large-v3" is on the row;
    # verify the page doesn't crash and the model name is accessible.
    # (We don't assert UI text since the design is in flux.)
    assert "distil-large-v3" in resp.text or "local-best-and-fast" in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 11. 100% coverage: defensive branches
# ─────────────────────────────────────────────────────────────────────────────


def test_get_default_falls_back_to_base_when_registry_broken(monkeypatch):
    """If MODEL_REGISTRY is somehow missing 'local-best-and-fast',
    get_default_model_choice() must fall back to 'base' (defensive).

    We can't actually test the 'local-best-and-extremely-fast'
    missing path easily (is_mlx_available would return True and
    we'd early-return), so we only test the 'local-best-and-fast'
    missing path here.
    """
    from app.services import transcription
    # Make sure we're on the x86 path (no MLX), so we go down
    # the 'local-best-and-fast' branch.
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "x86_64")
    # Save + break
    saved = dict(transcription.MODEL_REGISTRY)
    del transcription.MODEL_REGISTRY["local-best-and-fast"]
    try:
        assert transcription.get_default_model_choice() == "base"
    finally:
        transcription.MODEL_REGISTRY.update(saved)


def test_transcribe_with_backend_rejects_unknown_backend(monkeypatch, tmp_path):
    """If the registry has a backend that's not 'faster-whisper' or
    'mlx-whisper', transcribe_with_backend raises ValueError
    (defensive). We register a fake 'bogus' backend and call
    transcribe_with_backend with it. The point is to never silently
    misroute a transcript.
    """
    import sys
    import types
    from app.services import transcription
    # Make MLX look available so we go down the mlx path normally
    # (we'll inject a bogus entry instead to test the else branch)
    monkeypatch.setattr("app.services.transcription.platform.machine", lambda: "arm64")

    saved = dict(transcription.MODEL_REGISTRY)
    transcription.MODEL_REGISTRY["__test_bogus__"] = {
        "label": "bogus",
        "model_id": "fake",
        "backend": "bogus-backend",  # not in {faster-whisper, mlx-whisper}
        "requires_mlx": False,
        "group": "smart",
    }
    try:
        # Need to bypass is_mlx_available check too (registry entry doesn't require_mlx)
        # But resolve_model_choice will go through normally since requires_mlx=False
        fake_file = tmp_path / "fake.mp4"
        fake_file.write_text("x")
        with pytest.raises(ValueError, match="Unknown backend"):
            transcription.transcribe_with_backend(
                str(fake_file), "__test_bogus__"
            )
    finally:
        # Restore registry
        for k in list(transcription.MODEL_REGISTRY.keys()):
            if k == "__test_bogus__":
                del transcription.MODEL_REGISTRY[k]
