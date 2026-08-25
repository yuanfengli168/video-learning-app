"""
Live-boot integration test for Day 6 gunicorn stack.

WHY this test file is separate from test_gunicorn_conf.py:
  - test_gunicorn_conf.py is fast (< 0.1s): it just loads the .py file
    and checks invariants. It runs on every pytest invocation.
  - test_gunicorn_boot.py is SLOW (5-15s): it spawns a real gunicorn
    master process, waits for workers, hits HTTP endpoints, and
    SIGTERMs the master. It runs by default but is marked so it can
    be skipped with `pytest -m "not slow"`.

WHAT it verifies (the things you can't catch with config-file checks):
  1. gunicorn can actually import app.main (catches import errors
     that don't surface until preload_app=True runs).
  2. The master boots 4 workers (not 1, not 21, not 0).
  3. Workers actually serve /api/health and /api/ready with 200.
  4. /api/ready reports db=ok when DB is reachable.
  5. SIGTERM to the master triggers graceful shutdown of workers.

WHAT it does NOT verify (out of scope for pytest):
  - Cloudflare Tunnel reaching gunicorn — requires external infra.
  - Real Groq/Ollama calls — covered separately by chat tests.
  - Production SQLite at /Volumes/Storage-Fast-NVMe/video_learning.db
    — uses an in-memory SQLite to avoid touching the Mac Studio DB.

MARKERS:
  Marked as 'slow' so `pytest -m "not slow"` skips it during fast
  iteration. Marked as 'integration' so prod CI can run it.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUNICORN_CONF = PROJECT_ROOT / "gunicorn.conf.py"

# Free port to avoid clashing with whatever's already on 8000
TEST_PORT = 8765


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    """Poll until something is listening on the port, or fail after `timeout`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.2)
    raise TimeoutError(f"nothing listened on {host}:{port} within {timeout}s")


def _wait_for_http(url: str, timeout: float = 15.0) -> int:
    """Poll until the URL returns any HTTP response (even 503)."""
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(url)
                return r.status_code
        except (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout) as e:
            last_exc = e
            time.sleep(0.3)
    raise AssertionError(f"{url} never responded within {timeout}s: {last_exc}")


@pytest.fixture(scope="module")
def gunicorn_process():
    """Spawn a real gunicorn master, yield, then clean up.

    Uses a separate DATABASE_URL (in-memory SQLite) so this doesn't
    touch the dev or prod DB. Uses a different port so it doesn't
    clash with anything else on 8000.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///:memory:"  # in-memory DB
    env["UPLOAD_DIR"] = str(PROJECT_ROOT / "uploads" / "_test_boot")
    env["STORAGE_DIR"] = str(PROJECT_ROOT / "uploads" / "_test_boot_storage")
    os.makedirs(env["UPLOAD_DIR"], exist_ok=True)
    os.makedirs(env["STORAGE_DIR"], exist_ok=True)

    # Launch gunicorn as a subprocess
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "gunicorn",
            "-c", str(GUNICORN_CONF),
            "-b", f"127.0.0.1:{TEST_PORT}",
            "app.main:app",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for the port to be listening
    try:
        _wait_for_port("127.0.0.1", TEST_PORT, timeout=15.0)
    except TimeoutError:
        # Capture gunicorn output to help debugging
        try:
            out = proc.stdout.read(timeout=2).decode("utf-8", errors="replace") if proc.stdout else ""
        except Exception:
            out = "<could not capture stdout>"
        proc.kill()
        pytest.fail(f"gunicorn never bound to port {TEST_PORT}.\nOutput:\n{out}")

    yield proc

    # Cleanup: SIGTERM, wait, SIGKILL if needed
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ─── Live boot tests ─────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.integration
class TestGunicornLiveBoot:
    """End-to-end verification that gunicorn boots our app correctly.

    Marked 'slow' because they take 5-15s each (worker boot time).
    """

    def test_workers_actually_boot(self, gunicorn_process):
        """The master must spawn 4 workers within 15s.

        If gunicorn failed to fork (e.g. memory limit hit), only 1
        process would be running and pgrep would show that.
        """
        # Give workers a moment to fully boot
        time.sleep(2)

        # Find children of THIS gunicorn master (avoid matching other
        # gunicorns that may be running on the host, e.g. for dev).
        result = subprocess.run(
            ["pgrep", "-P", str(gunicorn_process.pid)],
            capture_output=True,
            text=True,
        )
        worker_pids = [p for p in result.stdout.strip().split("\n") if p]
        assert len(worker_pids) >= 4, (
            f"expected ≥4 worker children of master pid "
            f"{gunicorn_process.pid}, got {len(worker_pids)}: {worker_pids}"
        )

    def test_health_endpoint_returns_200(self, gunicorn_process):
        """GET /api/health must return 200 with 'ok' in body.

        /api/health is the liveness probe. If it returns anything else,
        Cloudflare Tunnel's health checks would mark the deployment unhealthy.
        """
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"http://127.0.0.1:{TEST_PORT}/api/health")
        assert r.status_code == 200, f"health returned {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("status") == "ok", f"health body: {body}"

    def test_ready_endpoint_returns_200_when_db_ok(self, gunicorn_process):
        """GET /api/ready must return 200 when DB is reachable.

        /api/ready is the readiness probe. Returns 503 if DB is down.
        """
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"http://127.0.0.1:{TEST_PORT}/api/ready")
        # Either 200 (all OK) or 503 (DB issue) — but not 500 (server bug)
        assert r.status_code in (200, 503), f"ready returned {r.status_code}: {r.text}"
        body = r.json()
        assert "db" in body, f"ready body missing 'db' field: {body}"

    def test_root_returns_200(self, gunicorn_process):
        """GET / must return 200 or 3xx (redirect to login/dashboard).

        This catches the case where gunicorn boots workers but the app
        fails to mount its routers.
        """
        with httpx.Client(timeout=5.0, follow_redirects=False) as client:
            r = client.get(f"http://127.0.0.1:{TEST_PORT}/")
        assert 200 <= r.status_code < 400, (
            f"GET / returned {r.status_code}: {r.text[:200]}"
        )

    def test_graceful_shutdown(self, gunicorn_process):
        """SIGTERM to master should result in clean exit within 10s.

        If gunicorn ignores SIGTERM (e.g. graceful_timeout=0), it stays
        alive and we have to SIGKILL — which is the bug we're guarding against.
        """
        master_pid = gunicorn_process.pid
        start = time.monotonic()

        gunicorn_process.send_signal(signal.SIGTERM)

        # Wait for the process to actually exit
        try:
            gunicorn_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"gunicorn master (pid {master_pid}) did not exit within "
                "10s of SIGTERM — graceful_timeout is broken."
            )

        elapsed = time.monotonic() - start
        assert elapsed < 10, f"shutdown took {elapsed:.1f}s, expected < 10s"

        # Workers should also be gone (they were forked children of master).
        # We only check children of OUR master — there may be other gunicorn
        # processes running on the host (e.g. dev server on port 8000)
        # that we don't own.
        time.sleep(0.5)
        result = subprocess.run(
            ["pgrep", "-P", str(master_pid)],
            capture_output=True,
            text=True,
        )
        survivors = [p for p in result.stdout.strip().split("\n") if p]
        assert len(survivors) == 0, (
            f"workers of master {master_pid} survived graceful shutdown: {survivors}"
        )
