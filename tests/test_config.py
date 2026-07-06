"""Tests for app.config.Settings."""

import os
from app.config import Settings


def test_default_settings(monkeypatch):
    """Settings should have sensible defaults (when no env overrides)."""
    # Clear env vars that conftest may have set
    for key in ["DEBUG", "DATABASE_URL", "UPLOAD_DIR", "STORAGE_DIR"]:
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.app_name == "Video Learning App"
    assert s.debug is True
    assert s.database_url.startswith("sqlite")
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.ollama_model == "glm-5.2:cloud"


def test_settings_from_env(monkeypatch):
    """Settings should load from environment variables."""
    monkeypatch.setenv("APP_NAME", "Test App")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    s = Settings()
    assert s.app_name == "Test App"
    assert s.debug is False
    assert s.ollama_model == "test-model"


def test_upload_path_creates_dir(tmp_path, monkeypatch):
    """upload_path should create the directory if it doesn't exist."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    s = Settings()
    path = s.upload_path
    assert path.exists()
    assert path.is_dir()


def test_storage_path_creates_dir(tmp_path, monkeypatch):
    """storage_path should create the directory if it doesn't exist."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    s = Settings()
    path = s.storage_path
    assert path.exists()
    assert path.is_dir()