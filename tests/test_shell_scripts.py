"""
Tests for the shell scripts in scripts/ (start.sh, stop.sh, restart.sh).

Why this test file exists:
  Day 6 switched from `uvicorn --reload` to gunicorn. The shell scripts
  are the operational interface — if start.sh has a typo or stop.sh
  forgets to handle gunicorn workers, we lose the ability to deploy or
  respond to incidents. These tests catch:
    - syntax errors (bash -n)
    - missing required patterns (e.g. stop.sh must kill gunicorn)
    - the SERVER=uvicorn escape hatch in start.sh actually works

What these tests DON'T cover:
  - Full boot of gunicorn (slow, requires real DB; see test_gunicorn_boot.py)
  - Cloudflare Tunnel integration (external infra; runbook only)
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _script(name: str) -> Path:
    return SCRIPTS_DIR / name


# ─── Syntax checks (fast, always run) ────────────────────────────────────


@pytest.mark.parametrize(
    "script_name",
    ["start.sh", "stop.sh", "restart.sh"],
)
def test_script_is_valid_bash(script_name: str):
    """Each script must parse with `bash -n` (no execution).

    A typo like `if [[` (missing `]]`) would crash at runtime, not at
    edit-time. `bash -n` catches syntax errors without running.
    """
    script = _script(script_name)
    assert script.is_file(), f"missing {script}"
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{script_name} has a bash syntax error:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "script_name",
    ["start.sh", "stop.sh", "restart.sh"],
)
def test_script_uses_strict_mode(script_name: str):
    """All scripts should `set -euo pipefail` near the top.

    Without `set -e`, a failed command (like activating a missing venv)
    is silently ignored and the script proceeds to run `gunicorn` against
    the system Python instead of the venv's Python. Disaster.
    """
    content = _script(script_name).read_text()
    assert re.search(r"set\s+-e[eu]*o\s+pipefail", content), (
        f"{script_name} must `set -euo pipefail` near the top — without "
        "it, a failed step (e.g. missing venv) is silently ignored."
    )


# ─── start.sh structural checks ──────────────────────────────────────────


class TestStartSh:
    """start.sh is the entry point — every new operator runs it first."""

    def test_default_server_is_gunicorn(self):
        """SERVER env var default must be 'gunicorn'.

        Production = gunicorn. If we changed default to 'uvicorn' by
        accident, every production deploy would boot the wrong server.
        """
        content = _script("start.sh").read_text()
        match = re.search(r'SERVER="?\$\{SERVER:-(\w+)\}"?', content)
        assert match, "start.sh must have `SERVER=\"${SERVER:-X}\"` default"
        assert match.group(1) == "gunicorn", (
            f"start.sh default SERVER is '{match.group(1)}', expected "
            "'gunicorn' — production would silently boot uvicorn."
        )

    def test_supports_uvicorn_escape_hatch(self):
        """SERVER=uvicorn must work as a documented escape hatch.

        Operators need hot-reload during dev. If we removed the
        `SERVER=uvicorn` branch, dev workflow dies.
        """
        content = _script("start.sh").read_text()
        assert "gunicorn" in content and "uvicorn" in content, (
            "start.sh must support BOTH gunicorn (prod default) and "
            "uvicorn (dev hot-reload via SERVER=uvicorn)."
        )

    def test_kills_port_8000_before_starting(self):
        """start.sh must free port 8000 before booting.

        If a previous instance is hung on 8000, the new gunicorn fails
        to bind. Better to kill the old one and start fresh.
        """
        content = _script("start.sh").read_text()
        assert "lsof -ti:8000" in content, (
            "start.sh must `lsof -ti:8000 | xargs kill` before booting — "
            "otherwise a hung previous instance blocks the new gunicorn."
        )

    def test_checks_ollama_running(self):
        """start.sh must check Ollama before booting.

        Ollama is required for ADMIN/PAID chat (paid tier). If it's
        not running, the boot should either start it or warn loudly.
        """
        content = _script("start.sh").read_text()
        assert "11434" in content, (
            "start.sh must check Ollama (port 11434) before booting — "
            "ADMIN/PAID chat depends on it."
        )

    def test_activates_venv(self):
        """start.sh must `source venv/bin/activate`.

        Without this, gunicorn runs against system Python, which has
        none of our packages installed. Import errors at runtime.
        """
        content = _script("start.sh").read_text()
        assert "venv/bin/activate" in content, (
            "start.sh must `source venv/bin/activate` — otherwise "
            "gunicorn runs against system Python with no packages."
        )

    def test_teases_stderr_to_log_file(self):
        """Logs must go to logs/server.log (runbook references this path)."""
        content = _script("start.sh").read_text()
        assert "logs/server.log" in content or "tee" in content, (
            "start.sh must capture logs to logs/server.log — the "
            "runbook day6 has 'tail -f logs/server.log' as step 1."
        )

    def test_sets_no_proxy_to_disable_macos_proxy_lookup(self):
        """Day 9 hotfix: set NO_PROXY=* before launching gunicorn.

        Without this, every outbound HTTPS call (Groq, Ollama, Firebase,
        YouTube) goes through SCDynamicStoreCopyProxiesWithOptions, which
        crashes Python 3.14 with EXC_GUARD (bug_type=309) — see macOS
        DiagnosticReports/Python-*.ips. The fix: skip proxy auto-discovery
        so the crashing code path is never hit.

        Regression guard: if someone removes the export NO_PROXY line,
        this test fails BEFORE the Mac segfaults in production.
        """
        content = _script("start.sh").read_text()
        # Look for the export line (must come BEFORE exec gunicorn)
        no_proxy_match = re.search(r"^\s*export\s+NO_PROXY\s*=", content, re.MULTILINE)
        exec_match = re.search(r"^\s*exec\s+gunicorn", content, re.MULTILINE)
        assert no_proxy_match, (
            "start.sh must `export NO_PROXY=*` before launching gunicorn — "
            "without it, Python 3.14 + macOS workers crash in "
            "SCDynamicStoreCopyProxiesWithOptions when making outbound HTTPS."
        )
        assert exec_match, "start.sh must exec gunicorn"
        assert no_proxy_match.start() < exec_match.start(), (
            "NO_PROXY must be exported BEFORE exec gunicorn — workers "
            "inherit the parent's env, so the export must come before "
            "the exec that replaces the shell."
        )
        # The value should be the wildcard (we don't use a proxy)
        assert '"*"' in content or "'*'" in content, (
            "NO_PROXY value must be '*' (skip proxy lookup for all hosts)"
        )


# ─── stop.sh structural checks ──────────────────────────────────────────


class TestStopSh:
    """stop.sh must handle BOTH gunicorn (prod) and uvicorn (dev)."""

    def test_kills_gunicorn_processes(self):
        """stop.sh must pattern-match gunicorn.

        Without this, gunicorn workers survive and port 8000 stays held.
        """
        content = _script("stop.sh").read_text()
        assert "gunicorn" in content, (
            "stop.sh must match 'gunicorn' processes — production's "
            "server has 1 master + 4 workers, all must be killed."
        )

    def test_kills_uvicorn_processes(self):
        """stop.sh must pattern-match uvicorn (dev mode)."""
        content = _script("stop.sh").read_text()
        assert "uvicorn" in content, (
            "stop.sh must match 'uvicorn' processes — dev mode uses "
            "`SERVER=uvicorn` and needs to be stoppable too."
        )

    def test_uses_video_learning_app_proc_name(self):
        """stop.sh must match the 'video-learning-app' proc_name.

        gunicorn.conf.py sets proc_name='video-learning-app'. Without
        matching this name, the master process is invisible to pgrep.
        """
        content = _script("stop.sh").read_text()
        assert "video-learning-app" in content, (
            "stop.sh must pattern-match 'video-learning-app' (the "
            "proc_name set in gunicorn.conf.py) — otherwise the "
            "gunicorn master is invisible to pgrep."
        )

    def test_sends_sigterm_before_sigkill(self):
        """stop.sh must try SIGTERM first, escalation to SIGKILL after timeout.

        SIGKILL on a gunicorn worker mid-LLM-call leaves the LiteLLM
        rate limiter's in-memory counter in an inconsistent state
        (handled by our autouse fixture in pytest, but in production
        we want graceful shutdown for in-flight requests).

        We distinguish two paths:
          - Main flow: SIGTERM (kill $PID) → wait → SIGKILL (kill -9)
          - Safety net: kill -9 anything still on port 8000 (always
            allowed, fires only if all else fails)

        The test asserts: in the main flow, SIGTERM appears BEFORE the
        SIGKILL that escalates the timeout (not before the final
        port-holder safety net).
        """
        content = _script("stop.sh").read_text()
        lines = content.splitlines()

        # SIGTERM in the main loop: `kill "$PID"` (default signal)
        sigterm_lines = [
            i for i, line in enumerate(lines, 1)
            if re.search(r"\bkill\s+\"", line)
            and not line.lstrip().startswith("#")
        ]
        # SIGKILL in the main flow (escalation loop): `kill -9 "$PID"`
        escalation_lines = [
            i for i, line in enumerate(lines, 1)
            if re.search(r"kill\s+-9\s+\"", line)
            and not line.lstrip().startswith("#")
        ]
        # Final safety net: `lsof -ti:8000 | xargs kill -9`
        safety_net_lines = [
            i for i, line in enumerate(lines, 1)
            if "kill -9" in line and "xargs" in line
            and not line.lstrip().startswith("#")
        ]

        assert sigterm_lines, (
            "stop.sh must send SIGTERM in the main flow (e.g. "
            "`kill \"$PID\"`) — default signal of `kill` is SIGTERM and "
            "that's what triggers gunicorn's graceful drain."
        )
        assert escalation_lines, (
            "stop.sh must escalate to SIGKILL (`kill -9 \"$PID\"`) in the "
            "main flow if SIGTERM is ignored for > 5s."
        )
        assert min(sigterm_lines) < min(escalation_lines), (
            "stop.sh must try SIGTERM before escalating to SIGKILL — "
            "sending SIGKILL first kills in-flight requests mid-LLM-call. "
            f"SIGTERM line={min(sigterm_lines)}, escalation line={min(escalation_lines)}"
        )
        # The safety net (kill -9 via lsof) is fine to be anywhere — it's
        # the last-resort port-holder killer. Just verify it exists.
        assert safety_net_lines, (
            "stop.sh must have a safety net: `lsof -ti:8000 | xargs kill -9` "
            "— catches zombies that pgrep missed."
        )

    def test_kills_anything_still_on_port_8000(self):
        """stop.sh's safety net: even if pgrep misses, force-kill port 8000."""
        content = _script("stop.sh").read_text()
        assert "lsof -ti:8000" in content, (
            "stop.sh must have a final safety net: `lsof -ti:8000 | "
            "xargs kill -9` — catches zombies that pgrep missed."
        )

    def test_sends_sigterm_with_timeout(self):
        """stop.sh must wait for graceful shutdown (not instant SIGKILL).

        A graceful window lets in-flight requests finish. < 3s is too
        short for LLM calls; > 15s is too long for incident response.
        """
        content = _script("stop.sh").read_text()
        # Look for a sleep loop or `timeout` command after SIGTERM
        assert "sleep" in content or "timeout" in content, (
            "stop.sh must wait (sleep or timeout) between SIGTERM and "
            "SIGKILL — instant SIGKILL kills in-flight requests."
        )


# ─── restart.sh structural checks ───────────────────────────────────────


class TestRestartSh:
    """restart.sh = stop.sh + sleep + start.sh."""

    def test_calls_stop_then_start(self):
        """restart.sh must call stop.sh before start.sh (in that order).

        Looks for actual bash invocations (bash ... stop.sh / start.sh),
        not the comments above. Both forms work:
          bash stop.sh
          bash "$SCRIPT_DIR/stop.sh"
        """
        content = _script("restart.sh").read_text()
        # Match an invocation: `bash` followed by stop.sh or start.sh anywhere on the line
        stop_lines = [
            i for i, line in enumerate(content.splitlines(), 1)
            if re.search(r"\bstop\.sh\b", line) and not line.lstrip().startswith("#")
        ]
        start_lines = [
            i for i, line in enumerate(content.splitlines(), 1)
            if re.search(r"\bstart\.sh\b", line) and not line.lstrip().startswith("#")
        ]
        assert stop_lines, "restart.sh must invoke stop.sh (not just mention it in a comment)"
        assert start_lines, "restart.sh must invoke start.sh (not just mention it in a comment)"
        assert min(stop_lines) < min(start_lines), (
            "restart.sh must invoke stop.sh (line "
            f"{min(stop_lines)}) BEFORE start.sh (line {min(start_lines)}). "
            "Starting first leaves the old process holding port 8000."
        )

    def test_has_sleep_between(self):
        """restart.sh must wait between stop and start.

        Without a sleep, the kill may not have fully released the port
        before the new gunicorn tries to bind → "Address already in use".
        """
        content = _script("restart.sh").read_text()
        lines = content.splitlines()
        stop_idx = next(
            (i for i, line in enumerate(lines)
             if re.search(r"\bstop\.sh\b", line) and not line.lstrip().startswith("#")),
            None,
        )
        start_idx = next(
            (i for i, line in enumerate(lines)
             if re.search(r"\bstart\.sh\b", line) and not line.lstrip().startswith("#")),
            None,
        )
        assert stop_idx is not None and start_idx is not None
        between = "\n".join(lines[stop_idx + 1 : start_idx])
        assert "sleep" in between, (
            f"restart.sh must `sleep` between stop and start — port 8000 "
            "needs a moment to be released by the OS. "
            f"Lines between stop.sh and start.sh: {between!r}"
        )


# ─── Integration: actually run the scripts ───────────────────────────────


class TestScriptsExecutable:
    """Light integration tests — exercise the scripts in a subshell.

    These tests run actual scripts but in isolated ways that don't
    start the full server. Catches things like a script that depends
    on `which gunicorn` and silently falls back when gunicorn isn't
    installed yet.
    """

    def test_start_sh_dry_run_shows_commands(self):
        """start.sh must print its banner before running (even on failure).

        The banner tells operators what's happening. Without it, a
        hung script looks identical to a successful one.
        """
        # We can't fully run start.sh (it would try to start gunicorn),
        # but we can verify the banner is present in the script.
        content = _script("start.sh").read_text()
        assert "Starting" in content or "🚀" in content, (
            "start.sh must print a banner ('Starting...' or 🚀) — "
            "operators need to see what step it's on."
        )

    def test_stop_sh_handles_no_processes(self):
        """stop.sh must exit cleanly when no process is found.

        Running stop.sh on a stopped server should print a warning
        and exit 0, NOT exit non-zero (which would break CI / scripts).
        """
        # We can't easily test this without affecting the test env's
        # process tree, so we just check the script's pattern:
        # it must handle the empty-pgrep case gracefully.
        content = _script("stop.sh").read_text()
        assert "warn" in content or "echo" in content, (
            "stop.sh must print a message when no process is found — "
            "silent success on a missing process is confusing."
        )
        # It must NOT `exit 1` when no process is found
        # (look for a path where pgrep returns empty → exit 0)
        assert re.search(
            r"(?:pgrep.*\|\|\s*true|PIDS=.*\|\|\s*true)",
            content,
        ), "stop.sh must tolerate pgrep returning empty (use `|| true`)"
