"""Tests for the /pricing page (2026-09-05).

The dashboard's FREE-upgrade CTA links to /pricing — before this
page existed it 404'd (user-reported). The beta page must:
  1. Render 200 for signed-out visitors (public page).
  2. Show the beta banner + the contact email for non-paid users.
  3. Show "You're in! 🎉" for PAID/ADMIN instead of the contact CTA.
  4. Show "Your current plan" on the Free card for FREE users.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

FAKE_USER = {"uid": "test-user-uid", "email": "test@example.com"}


def _auth_headers():
    return {"Authorization": "Bearer fake-token"}


def _mock_auth():
    return patch(
        "app.auth.dependencies.verify_token",
        return_value=FAKE_USER,
    )


def test_pricing_page_public(client: TestClient):
    """Signed-out visitors can view the pricing page."""
    client.cookies.clear()
    resp = client.get("/pricing")
    assert resp.status_code == 200
    # Beta banner + both plans render
    assert "beta" in resp.text.lower()
    assert "Free" in resp.text
    assert "Paid" in resp.text


def test_pricing_shows_contact_for_free(client: TestClient):
    """FREE users see the email CTA + 'Your current plan' badge."""
    with _mock_auth():
        resp = client.get("/pricing", headers=_auth_headers())
    assert resp.status_code == 200
    assert "jackyopenclaw.168@gmail.com" in resp.text
    assert "Your current plan" in resp.text
    # The mailto CTA is present for non-paid users
    assert "mailto:jackyopenclaw.168@gmail.com" in resp.text
    assert "quite limited" in resp.text


def test_pricing_paid_sees_in(paid_client: TestClient):
    """PAID users see 'You're in!' instead of the contact CTA."""
    with _mock_auth():
        resp = paid_client.get("/pricing", headers=_auth_headers())
    assert resp.status_code == 200
    assert "You're in" in resp.text
    # No mailto CTA on the paid card for paid users
    assert "Try the paid version" not in resp.text


def test_pricing_admin_sees_in(admin_client: TestClient):
    with _mock_auth():
        resp = admin_client.get("/pricing", headers=_auth_headers())
    assert resp.status_code == 200
    assert "You're in" in resp.text