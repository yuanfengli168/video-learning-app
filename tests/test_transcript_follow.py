"""Tests for app/static/js/transcript-follow.js (MVP2.0 item #2).

The JS code is small and lives in a static file (not a Python module),
so we test it two ways:

1. Pure helper + integration coverage via `node --test tests/test_transcript_follow.mjs`.
   The .mjs file re-loads the production source in a sandbox and asserts
   on `window.TranscriptFollow._internals` (pure helpers) plus a small
   DOM shim for integration (timeupdate / seeked / hover / rAF / destroy).
   This is run via subprocess below and the TAP output is parsed to
   make individual test names visible in `pytest -v`.

2. Integration coverage: we assert the video page HTML loads the script
   + CSS, and the static mount serves the files. These live in
   `tests/test_frontend.py` and `tests/test_main.py`.

The MVP1.1 surface (smart/always dropdown, localStorage, setMode/getMode,
shouldScroll, storageKey, readPersistedMode, writePersistedMode) is gone
in MVP2.0. The new surface is just init + destroy, with findActiveSegment
as the only _internals helper.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_TEST_FILE = REPO_ROOT / "tests" / "test_transcript_follow.mjs"


def _has_node() -> bool:
    return shutil.which("node") is not None


# Skip the entire module if node isn't on PATH (e.g. minimal CI image).
pytestmark = pytest.mark.skipif(
    not _has_node(), reason="node not available; cannot run JS test suite"
)


def _run_node_tests() -> tuple[int, str]:
    """Run `node --test` on the .mjs file and return (exit_code, stdout)."""
    proc = subprocess.run(
        ["node", "--test", str(JS_TEST_FILE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_node_test_suite_exits_zero():
    """The full node --test suite must pass (17 tests at the time of writing)."""
    rc, output = _run_node_tests()
    assert rc == 0, (
        "node --test failed.\n"
        f"--- output ---\n{output}\n--- end output ---"
    )
    # Sanity: TAP summary line.
    assert "# pass " in output, (
        f"Expected '# pass' in TAP output, got:\n{output}"
    )


def _parse_tap_test_names(output: str) -> list[tuple[bool, str]]:
    """Return [(passed, name), ...] for every `ok`/`not ok` line."""
    results: list[tuple[bool, str]] = []
    for line in output.splitlines():
        m = re.match(r"^(ok|not ok)\s+\d+\s+-\s+(.*)$", line)
        if m:
            passed = m.group(1) == "ok"
            results.append((passed, m.group(2).strip()))
    return results


def test_node_tests_cover_pure_helpers():
    """Sanity check: every public pure helper has at least one passing test."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    # findActiveSegment is the only _internals helper now.
    assert any("findActiveSegment" in n for n in names), names


def test_node_tests_cover_source_level_regressions():
    """The source-level guards (no scrollIntoView, no smart/always
    API) must be tested. These are the canaries that catch accidental
    re-introductions of the removed behavior."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("scrollIntoView" in n for n in names), names
    assert any("smart/always" in n for n in names), names
    assert any("seeked" in n for n in names), names
    assert any("hover-to-pause" in n for n in names), names
    assert any("rAF" in n for n in names), names


def test_node_tests_cover_integration():
    """The integration tests (timeupdate, seeked, hover, rAF batching,
    destroy, re-init) must all be present and passing."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("timeupdate highlights" in n for n in names), names
    assert any("seeked" in n and "without needing timeupdate" in n for n in names), names
    assert any("mouseenter pauses" in n for n in names), names
    assert any("rAF batches" in n for n in names), names
    assert any("destroy" in n for n in names), names
    assert any("re-init" in n for n in names), names


def test_node_tests_cover_public_surface():
    """The new MVP2.0 surface (init + destroy only, no setMode/getMode)
    must be locked by tests so a regression fails in CI."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("public surface" in n for n in names), names
    assert any("_internals" in n for n in names), names
