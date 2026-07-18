"""Tests for the Plugin Tools registry (MVP2.1.0).

The registry is a dict of named plugins. v1 ships with one
plugin: WebM -> MP4. Future plugins (audio extraction,
metadata fix, etc.) will be added by appending to
PLUGIN_REGISTRY. These tests guard the registry's shape so
adding new plugins doesn't accidentally break the UI's
expectations.
"""

from __future__ import annotations

from app.services.plugins import (
    PLUGIN_REGISTRY,
    PluginSpec,
    get_plugin,
    is_ffmpeg_available,
    list_available_plugins,
)


# ── Registry shape ──────────────────────────────────────────────────────
def test_registry_has_webm_to_mp4_plugin():
    """v1 ships with exactly one plugin: WebM -> MP4."""
    assert "webm_to_mp4" in PLUGIN_REGISTRY
    spec = PLUGIN_REGISTRY["webm_to_mp4"]
    assert isinstance(spec, PluginSpec)
    assert spec.key == "webm_to_mp4"
    assert spec.label  # non-empty
    assert spec.description  # non-empty
    assert spec.function is not None  # the function pointer is set


def test_registry_plugin_has_required_fields():
    """Every plugin must have a label + description + function.

    These are the fields the Tools tab UI uses to render
    the button + tooltip. If any are missing the UI
    would render an empty card.
    """
    for key, spec in PLUGIN_REGISTRY.items():
        assert spec.label, f"Plugin {key!r} has no label"
        assert spec.description, f"Plugin {key!r} has no description"
        assert spec.function is not None, (
            f"Plugin {key!r} has no function pointer"
        )


def test_registry_keys_are_unique():
    """Plugin keys must be unique — the key is the URL slug."""
    keys = list(PLUGIN_REGISTRY.keys())
    assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"


def test_registry_keys_are_url_safe():
    """Plugin keys appear in URLs (/api/plugins/{key}/run).

    Only lowercase letters, digits, and underscores.
    """
    import re

    for key in PLUGIN_REGISTRY:
        assert re.match(r"^[a-z][a-z0-9_]*$", key), (
            f"Plugin key {key!r} is not URL-safe "
            f"(use lowercase + digits + underscores only)"
        )


def test_webm_to_mp4_declares_ffmpeg_as_required():
    """The WebM -> MP4 plugin must declare ffmpeg in `requires`."""
    spec = PLUGIN_REGISTRY["webm_to_mp4"]
    assert "ffmpeg" in spec.requires


# ── Lookup functions ────────────────────────────────────────────────────
def test_get_plugin_returns_spec_for_known_key():
    """get_plugin('webm_to_mp4') returns the spec."""
    spec = get_plugin("webm_to_mp4")
    assert spec is not None
    assert spec.key == "webm_to_mp4"


def test_get_plugin_returns_none_for_unknown_key():
    """get_plugin('nonexistent') returns None (not raise)."""
    assert get_plugin("definitely_not_a_plugin") is None


def test_list_available_plugins_returns_all_registered():
    """list_available_plugins() returns one entry per registered plugin."""
    plugins = list_available_plugins()
    assert len(plugins) == len(PLUGIN_REGISTRY)
    # Each item is a PluginSpec
    for p in plugins:
        assert isinstance(p, PluginSpec)


# ── ffmpeg detection ────────────────────────────────────────────────────
def test_is_ffmpeg_available_returns_bool():
    """is_ffmpeg_available() returns True or False (not raise)."""
    result = is_ffmpeg_available()
    assert isinstance(result, bool)
