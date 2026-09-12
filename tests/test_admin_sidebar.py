"""Sidebar completeness on every admin page (2026-09-12).

User report: clicking 'LLM Budget', 'Audit Log' or 'Backup Health'
made the rest of the left sidebar (Analytics, Usage Monitor, etc.)
disappear, while other pages showed the full sidebar.

Root cause: those three routes called _ctx() WITHOUT `db`. _ctx only
computes `is_admin` + `user_capabilities` when a db session is
available (the role lookup needs it), so on those pages the template
rendered as if the viewer were anonymous/FREE — the whole admin nav
block `{% if is_admin %}` collapsed, along with the chat_paid-gated
'Your Activity' link.

The fix: pass db=db from all admin page routes. This test pins every
admin page's sidebar so a future route that forgets db (or any other
_ctx regression) fails here with a clear message instead of shipping
a half-rendered sidebar.
"""

import pytest
from fastapi.testclient import TestClient

ADMIN_PAGES = [
    "/admin/analytics",
    "/admin/usage",
    "/admin/events",
    "/admin/budget",
    "/admin/backups",
    "/admin/playback",
]

# Sidebar links that must render on every admin page. Matched by href
# (labels contain emoji spans, so text matching is brittle).
REQUIRED_SIDEBAR_HREFS = [
    "/admin/analytics",
    "/admin/usage",
    "/admin/events",
    "/admin/budget",
    "/admin/backups",
    "/admin/upload",
    "/admin/playback",
    "/chat-history",
    "/usage",
    "/activity",  # chat_paid-gated — admins have it; lost when db missing
]


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_sidebar_complete(admin_client: TestClient, path: str):
    resp = admin_client.get(path)
    assert resp.status_code == 200, f"{path}: {resp.status_code}"
    html = resp.text
    # Slice to the sidebar so a link mentioned in page CONTENT (e.g.
    # the analytics footer mentioning 'Audit Log') can't mask a
    # missing sidebar entry.
    start = html.find("<aside")
    end = html.find("</aside>")
    assert start >= 0 and end > start, f"{path}: sidebar <aside> not found"
    sidebar = html[start:end]
    missing = [h for h in REQUIRED_SIDEBAR_HREFS if f'href="{h}"' not in sidebar]
    assert not missing, f"{path}: sidebar missing {missing}"