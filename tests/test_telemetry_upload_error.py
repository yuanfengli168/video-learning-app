"""Tests for the ui.upload failure beacon (2026-09-22).

Backstory — the "two bulk uploads failed with some error but the
server log is clean" mystery: multi-file bulk bodies bundling
~18GB (18 files) and ~1.5GB (8 files) were rejected at
Cloudflare's edge (the free plan's 100MB per-REQUEST cap) BEFORE
ever reaching the server, so both incidents left ZERO server-side
trace. The ui.upload telemetry source closes that observability
gap: the browser beacon reports client-side upload failures
through the (tiny) telemetry POST, which always fits the tunnel.

Server contract (app/routers/telemetry.py):
  source        ui.upload
  video_id      — (none; failures predate any Video row)
  context       {path: single|chunked|bulk,
                 error: 1-200 chars,
                 filename?, file_count?}

These tests pin:
  1. Happy path — a well-formed ui.upload event lands in `events`
     with the grep-friendly "ui upload failure via <path>: …"
     message.
  2. Shape validation — unknown path / missing / oversized
     error → 400.
  3. The source is on the allowlist (and stays distinct from
     services.* forgery).
  4. Message formatting carries the path + reason.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.routers.telemetry import UI_EVENT_SOURCES, UI_UPLOAD_PATHS


FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_USER,
    )


def _upload_event(**overrides):
    ev = {
        "source": "ui.upload",
        "context": {
            "path": "bulk",
            "error": "Server error (413)",
            "filename": "lecture-01.mp4",
            "file_count": 18,
        },
    }
    ev.update(overrides)
    return ev


# ─────────────────────────────────────────────────────────────────────────────
# 1. Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_upload_failure_event_lands_in_events(client: TestClient, db_session):
    """A client-reported upload failure writes a row the admin can grep."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [_upload_event()]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 1

    rows = db_session.execute(
        text(
            "SELECT source, level, message, user_id, context_json "
            "FROM events WHERE source = 'ui.upload'"
        )
    ).fetchall()
    assert len(rows) == 1
    source, level, message, user_id, context_json = rows[0]
    assert source == "ui.upload"
    # Level is INFO by design: the beacon is observational ("the
    # browser told us it failed") — a failure the SERVER saw would be
    # its own server-side ERROR row.
    assert level == "INFO"
    # Grep-friendly message: path + reason both present.
    assert "ui upload failure via bulk" in message
    assert "Server error (413)" in message
    assert user_id == "test-user-uid"
    # The context (filename, file_count) serializes through.
    import json as _json
    ctx = _json.loads(context_json)
    assert ctx["filename"] == "lecture-01.mp4"
    assert ctx["file_count"] == 18
    assert ctx["path"] == "bulk"


def test_upload_failure_happy_paths_all_valid(client: TestClient):
    """Each documented path value (single/chunked/bulk) is accepted."""
    with _mock_auth():
        for path in ("single", "chunked", "bulk"):
            resp = client.post(
                "/api/telemetry",
                json={"events": [_upload_event(context={
                    "path": path, "error": "Network error during upload",
                })]},
                headers=_auth_headers(),
            )
            assert resp.status_code == 202


# ─────────────────────────────────────────────────────────────────────────────
# 2. Shape validation
# ─────────────────────────────────────────────────────────────────────────────

def test_upload_failure_rejects_unknown_path(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [_upload_event(context={
                "path": "quantum", "error": "boom",
            })]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "unknown upload path" in resp.json()["detail"]


def test_upload_failure_rejects_missing_error(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [{"source": "ui.upload",
                              "context": {"path": "bulk"}}]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    assert "context.error" in resp.json()["detail"]


def test_upload_failure_rejects_oversized_error_string(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [_upload_event(context={
                "path": "bulk", "error": "x" * 201,
            })]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_upload_failure_rejects_non_string_error(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [_upload_event(context={
                "path": "bulk", "error": 413,
            })]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


def test_upload_failure_rejects_empty_error(client: TestClient):
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [_upload_event(context={
                "path": "bulk", "error": "",
            })]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 3. Allowlist wiring
# ─────────────────────────────────────────────────────────────────────────────

def test_ui_upload_on_allowlist():
    """The source constants exist and are wired into the allowlist."""
    assert "ui.upload" in UI_EVENT_SOURCES
    assert UI_UPLOAD_PATHS == {"single", "chunked", "bulk"}


def test_ui_upload_distinct_from_services_forgery(client: TestClient, db_session):
    """ui.upload grants no path to forge services.* audit rows."""
    with _mock_auth():
        resp = client.post(
            "/api/telemetry",
            json={"events": [
                _upload_event(),
                {"source": "services.rerun_guards"},
            ]},
            headers=_auth_headers(),
        )
    assert resp.status_code == 400
    # All-or-nothing: the valid ui.upload row must NOT have been
    # written either (the batch is one transaction, validated before
    # any write happens).
    rows = db_session.execute(
        text("SELECT * FROM events WHERE source = 'ui.upload'")
    ).fetchall()
    assert rows == []