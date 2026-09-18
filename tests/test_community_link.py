"""Sidebar Community (Discord) link — visibility rules.

2026-09-18 rebrand batch: the public community invite is stored in
.env (DISCORD_INVITE_URL) and rendered by base.html as a 💬 Community
sidebar link — but ONLY for signed-in users, never for anonymous
visitors (a registered user is anyone with a Firebase account:
FREE, PAID, or ADMIN).

These tests lock in the three behaviors:
  1. signed-in + URL set        → link renders
  2. signed-in + URL empty      → link hidden (config off-switch)
  3. anonymous  + URL set       → link hidden (registration gate)
"""

import pytest
from fastapi.testclient import TestClient


def _ctx_settings(monkeypatch, invite_url: str):
    """Point the app's settings at the given community URL.

    frontend._ctx reads the `settings` SINGLETON at request time
    (app/config.py: `settings = Settings()`), so patching that
    instance is the correct seam. (Patching the Settings CLASS does
    not work — pydantic v2 model fields aren't plain class attrs.)
    """
    from app.config import settings
    monkeypatch.setattr(settings, "community_invite_url", invite_url)


def test_signed_in_user_sees_community_link(client: TestClient, monkeypatch):
    _ctx_settings(monkeypatch, "https://discord.gg/testinvite")
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Community" in body
    assert "https://discord.gg/testinvite" in body


def test_signed_in_user_no_link_when_url_empty(client: TestClient, monkeypatch):
    _ctx_settings(monkeypatch, "")
    r = client.get("/")
    assert r.status_code == 200
    assert "https://discord.gg" not in r.text


def test_anonymous_user_never_sees_community_link(
    client: TestClient, monkeypatch
):
    # The conftest client authenticates by default. Clear the cookie
    # jar for a truly anonymous request (established pattern: see
    # test_session_expiry_middleware.py — cookies={} MERGES with the
    # jar, only .clear() actually removes the session cookie).
    _ctx_settings(monkeypatch, "https://discord.gg/testinvite")
    client.cookies.clear()
    r = client.get("/")
    assert r.status_code == 200
    assert "https://discord.gg" not in r.text
    assert "Community" not in r.text