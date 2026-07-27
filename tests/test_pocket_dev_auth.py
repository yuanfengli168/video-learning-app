"""Tests for the dev-only auth bypass.

POCKET_DEV_AUTH=1 + X-Dev-User-Id header → trust the header.
Without either, 401.
"""

import os

# Set the env var BEFORE importing the app so dev_auth picks it up at import time.
os.environ["POCKET_DEV_AUTH"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Course


@pytest.fixture
def auth_client(db_session):
    """TestClient with POCKET_DEV_AUTH=1 already in env. No mock needed."""
    # Clear any leftover dependency overrides from other fixtures
    app.dependency_overrides.clear()
    # Re-import dev_auth to pick up the env var set above
    import importlib
    from app.pocket import dev_auth
    importlib.reload(dev_auth)
    # Re-mount the pocket router so the refreshed dependency is in effect
    with TestClient(app) as client:
        yield client


def test_dev_header_works_without_mock(auth_client, db_session):
    db = db_session
    c = Course(user_id="dev-test-uid-123", title="DevAuth Course")
    db.add(c)
    db.commit()

    r = auth_client.get(
        "/m/snapshot",
        headers={"X-Dev-User-Id": "dev-test-uid-123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(co["title"] == "DevAuth Course" for co in body["courses"])


def test_no_dev_header_returns_401_even_when_dev_auth_enabled(auth_client, db_session):
    r = auth_client.get("/m/snapshot")
    assert r.status_code == 401


def test_other_user_data_not_visible(auth_client, db_session):
    """A different user_id gets their own snapshot, not someone else's."""
    db = db_session
    db.add(Course(user_id="user-A", title="A's course"))
    db.add(Course(user_id="user-B", title="B's course"))
    db.commit()

    rA = auth_client.get("/m/snapshot", headers={"X-Dev-User-Id": "user-A"})
    rB = auth_client.get("/m/snapshot", headers={"X-Dev-User-Id": "user-B"})
    assert rA.status_code == 200 and rB.status_code == 200
    titlesA = [c["title"] for c in rA.json()["courses"]]
    titlesB = [c["title"] for c in rB.json()["courses"]]
    assert "A's course" in titlesA
    assert "A's course" not in titlesB
    assert "B's course" in titlesB
    assert "B's course" not in titlesA
