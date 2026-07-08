"""Python wrapper for the JS DOM tests in test_loadSummary_dom.mjs.

The actual tests run via `node --test` (see the .mjs file). This
file gives pytest visibility into them and adds the same pass/fail
assertions as a regular pytest run.

Run directly with: `node --test tests/test_loadSummary_dom.mjs`
or via pytest: `pytest tests/test_loadSummary_dom.py`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_TEST_FILE = REPO_ROOT / "tests" / "test_loadSummary_dom.mjs"


def _has_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _has_node(), reason="node not available; cannot run JS DOM test suite"
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


def _parse_tap(output: str) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    for line in output.splitlines():
        m = re.match(r"^(ok|not ok)\s+\d+\s+-\s+(.*)$", line)
        if m:
            results.append((m.group(1) == "ok", m.group(2).strip()))
    return results


def test_loadSummary_dom_suite_exits_zero():
    """All 4 loadSummary DOM tests must pass."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    assert "# pass 4" in output, output


def test_loadSummary_dom_covers_dont_stomp_ssr_branch():
    """The test named 'does NOT stomp the DOM' is the actual fix for
    Optimization #2 — make sure it ran."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [n for _, n in _parse_tap(output)]
    assert any("does NOT stomp the DOM" in n for n in names), names


def test_loadSummary_dom_covers_generate_button_fallback():
    """The negative case — !resp.ok with no SSR must show the
    Generate button — protects against an over-correction that
    would hide the button for fresh videos."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [n for _, n in _parse_tap(output)]
    assert any("no SSR renders the Generate button" in n for n in names), names


def test_loadSummary_dom_covers_cache_hit_short_circuit():
    """When contentCache.summary is already populated, loadSummary
    must not fire a fetch — this is the frontend half of the SSR fix
    (the page-init code seeds the cache from the SSR'd HTML)."""
    rc, output = _run_node_tests()
    assert rc == 0, output
    names = [n for _, n in _parse_tap(output)]
    assert any("cache hit short-circuits" in n for n in names), names
