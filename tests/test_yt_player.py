"""Tests for app/static/js/yt_player.js (Day 8: unified player wrapper).

Mirrors tests/test_transcript_follow.py: the actual JS tests live in
test_yt_player.mjs and are run via `node --test`. This wrapper:
  1. Verifies the .mjs suite passes end-to-end (catches accidental
     test regressions that would otherwise silently rot).
  2. Verifies the video.html template includes the script + iframe id
     + YTPlayer.init() bootstrap, so a deploy-time HTML regression
     breaks loudly here rather than at 11pm.

Why two layers:
  - .mjs = functional coverage (storage, idempotency, backend detection)
  - .py = integration glue (template wiring + .mjs subprocess)

Run with: pytest tests/test_yt_player.py -v
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_TEST_FILE = REPO_ROOT / "tests" / "test_yt_player.mjs"
JS_SOURCE_FILE = REPO_ROOT / "app" / "static" / "js" / "yt_player.js"
VIDEO_TEMPLATE = REPO_ROOT / "app" / "templates" / "video.html"


def _has_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _has_node(), reason="node not available; cannot run JS test suite"
)


def _run_node_tests() -> tuple[int, str]:
    proc = subprocess.run(
        ["node", "--test", str(JS_TEST_FILE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_node_test_suite_exits_zero():
    """The full node --test suite must pass.

    We don't pin the exact test count (Day 8 added 19, more may come
    in Day 9+), but we assert # pass > 0 and the exit code is 0.
    """
    rc, output = _run_node_tests()
    assert rc == 0, (
        f"node --test failed.\n--- output ---\n{output}\n--- end output ---"
    )
    # Sanity: TAP summary line.
    assert "# pass " in output, (
        f"Expected '# pass' in TAP output, got:\n{output}"
    )


def _parse_tap_test_names(output: str) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for line in output.splitlines():
        m = re.match(r"^(ok|not ok)\s+\d+\s+-\s+(.*)$", line)
        if m:
            passed = m.group(1) == "ok"
            results.append((passed, m.group(2).strip()))
    return results


def test_node_tests_cover_storage_helpers():
    """The storage sanitization + save/load helpers must be tested.

    These are the security boundary against localStorage poisoning;
    regressions here would let attacker-controlled video IDs corrupt
    other videos' resume positions.
    """
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("storageKey" in n for n in names), names
    assert any("savePosition" in n for n in names), names
    assert any("loadPosition" in n for n in names), names
    assert any("disabled localStorage" in n for n in names), names


def test_node_tests_cover_backend_detection():
    """Backend detection (iframe vs <video> vs neither) must be tested.

    This is the foundation of the whole wrapper — get it wrong and
    every other feature is broken.
    """
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("iframe" in n for n in names), names
    assert any("<video>" in n for n in names), names
    assert any("null backend" in n for n in names), names


def test_node_tests_cover_source_level_regressions():
    """Source-level guards must be tested (canary against regressions).

    - No querySelector('video') outside detectBackend()
    - Native backend doesn't load the YouTube iframe_api
    - Public API surface is stable
    """
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("querySelector" in n for n in names), names
    assert any("iframe_api" in n for n in names), names
    assert any("public API" in n for n in names), names


def test_node_tests_cover_enable_resume():
    """The auto-resume feature must be tested.

    Critical for the Day 8 UX promise (user returns to the page,
    finds the video where they left off).
    """
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("enableResume" in n or "resume" in n.lower() for n in names), names


# ── Video template integration ──────────────────────────────────────────


def test_video_template_loads_yt_player_script():
    """video.html must load yt_player.js before any script that needs it.

    Order matters: yt_player.js MUST come before transcript-follow.js
    (the latter uses the wrapper if available). And the inline <script>
    must reference YTPlayer.init().
    """
    text = VIDEO_TEMPLATE.read_text()
    yt_idx = text.find('yt_player.js')
    tf_idx = text.find('transcript-follow.js')
    assert yt_idx > -1, "video.html must include yt_player.js"
    assert tf_idx > -1, "video.html must still include transcript-follow.js"
    assert yt_idx < tf_idx, (
        "yt_player.js must load BEFORE transcript-follow.js "
        "so the wrapper is available when transcript-follow wires up."
    )
    assert 'YTPlayer.init()' in text, (
        "video.html must call YTPlayer.init() to bootstrap the wrapper"
    )


def test_video_template_iframe_has_youtube_player_id():
    """The YouTube iframe must have id='youtube-player' so YTPlayer
    can detect it via document.getElementById."""
    text = VIDEO_TEMPLATE.read_text()
    # Match the iframe block (Day 8: added the id)
    assert 'id="youtube-player"' in text, (
        "YouTube iframe in video.html must have id='youtube-player'"
    )
    assert 'enablejsapi=1' in text, (
        "YouTube iframe src must include enablejsapi=1 so postMessage works"
    )


def test_video_template_seek_uses_ytplayer_first():
    """Regression guard: after Day 8, video.html's seekTo() and the
    mindmap jump handler must TRY YTPlayer first, then fall back to
    document.querySelector('video') only if YTPlayer isn't ready.

    We assert by source-level grep: the primary path uses YTPlayer, and
    the fallback uses querySelector('video').
    """
    text = VIDEO_TEMPLATE.read_text()
    # Count occurrences of querySelector('video'). Acceptable uses:
    #  - initFollow fallback (transcript-follow wiring when YTPlayer isn't ready)
    #  - seekTo fallback (brief warmup window)
    #  - mindmap jump fallback (same warmup window)
    #  - swap modal (legacy <video>-only path, intentional)
    # What we DON'T accept: silent fallback that completely bypasses YTPlayer.
    fallback_count = text.count("querySelector('video')")
    ytplayer_count = text.count('YTPlayer')
    assert ytplayer_count >= 5, (
        f"video.html should reference YTPlayer ≥5 times (init, getInstance, "
        f"play, pause, seekTo). Found {ytplayer_count}. Make sure the "
        "primary seek path uses the wrapper, not direct DOM access."
    )
    # The fallback uses are legitimate, but they must be guarded by an
    # `if (window.YTPlayer && YTPlayer.getInstance())` check, not silent.
    assert fallback_count <= 4, (
        f"Found {fallback_count} querySelector('video') in video.html. "
        "This is too many — Day 8 should have collapsed most to YTPlayer. "
        "If you added new ones, ensure they fall back only after checking "
        "YTPlayer.getInstance() first."
    )


def test_static_mount_serves_yt_player_js():
    """The static mount in app/main.py must serve /static/js/yt_player.js.

    If the file exists on disk but isn't served, the page won't load
    the wrapper and all Day 8 features silently break.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/static/js/yt_player.js")
    assert r.status_code == 200, (
        f"/static/js/yt_player.js returned {r.status_code}. "
        "Check that the static mount includes js/."
    )
    assert "YTPlayer" in r.text, (
        "yt_player.js must export the YTPlayer global"
    )
