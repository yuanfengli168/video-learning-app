"""Tests for app/static/js/transcript-follow.js.

The JS code is small and lives in a static file (not a Python module),
so we test it two ways:

1. Pure helper coverage via `node --test tests/test_transcript_follow.mjs`.
   The .mjs file re-loads the production source in a sandbox and asserts
   on `window.TranscriptFollow._internals`. This is run via subprocess
   below and the TAP output is parsed to make individual test names
   visible in `pytest -v`.

2. Integration coverage: we assert the video page HTML loads the script
   + CSS, the dropdown is present, and the static mount serves the
   files. These live in `tests/test_frontend.py` and `tests/test_main.py`.
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
    """The full node --test suite must pass (13 tests at the time of writing)."""
    rc, output = _run_node_tests()
    assert rc == 0, (
        "node --test failed.\n"
        f"--- output ---\n{output}\n--- end output ---"
    )
    # Sanity: TAP summary line.
    assert "# pass 13" in output or "# pass " in output, (
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


def test_node_tests_cover_findActiveSegment_branches():
    """Sanity check: every public helper has at least one passing test."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("findActiveSegment" in n for n in names), names
    assert any("shouldScroll" in n for n in names), names
    assert any("storageKey" in n for n in names), names
    assert any("readPersistedMode" in n for n in names), names
    assert any("public surface" in n for n in names), names


def test_node_tests_cover_buffer_edge_cases():
    """The 20% safe-zone buffer behavior is the experiment's contract;
    make sure both the 'in safe zone' and 'out of safe zone' cases are
    exercised."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [name for _, name in _parse_tap_test_names(output)]
    assert any("smart" in n and "safe zone" in n for n in names), names
    assert any("above the safe zone" in n for n in names), names
    assert any("below the safe zone" in n for n in names), names
