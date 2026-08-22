"""Tests for app/auth/admin.py — DB lookup, caching, decorators.

Coverage:
- ensure_user_row: idempotent insert, upsert, integrity error handling
- get_user_role_from_db: missing user returns FREE, role lookup works
- _lookup_role_cached: lru_cache behavior, role_db in key invalidates
- clear_role_cache: clears the cache
- require_capability: 403 for non-admin, passes for admin
- require_admin: 403 for non-admin, passes for admin
- End-to-end via test client: admin-only endpoint returns 200/403

Strategy:
- For pure-function tests (lookup, cache, ensure_user_row), use db_session
  fixture directly with no HTTP.
- For decorator tests, build a tiny FastAPI app with one protected
  endpoint, mount both require_admin and require_capability, and call
  via TestClient with mocked verify_token (via unittest.mock.patch).
"""

import pytest
from unittest.mock import patch
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.admin import (
    _lookup_role_cached,
    clear_role_cache,
    ensure_user_row,
    get_user_role_from_db,
    require_admin,
    require_capability,
)
from app.auth.roles import Capability, UserRole


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_role_cache_between_tests():
    """Each test starts with an empty role cache.

    Autouse so we don't have to remember to call it. Without this,
    a role set in test A could leak into test B's lookup.
    """
    clear_role_cache()
    yield
    clear_role_cache()


def _make_protected_app():
    """Create a tiny FastAPI app with admin-only + capability-only endpoints."""
    test_app = FastAPI()

    @test_app.get("/admin-only")
    def admin_only(user=Depends(require_admin)):
        return {"uid": user.get("uid"), "is_admin": True}

    @test_app.get("/curate")
    def curate(user=Depends(require_capability(Capability.CURATE_CATALOG))):
        return {"uid": user.get("uid"), "can_curate": True}

    @test_app.get("/view")
    def view(user=Depends(require_capability(Capability.VIEW_VIDEO))):
        return {"uid": user.get("uid"), "can_view": True}

    @test_app.get("/no-auth")
    def no_auth():
        return {"ok": True}

    return test_app


def _fake_token(uid: str, email: str, extra_claims: dict | None = None):
    """Build a fake claims dict for verify_token mock."""
    claims = {"uid": uid, "email": email}
    if extra_claims:
        claims.update(extra_claims)
    return claims


# ─────────────────────────────────────────────────────────────────────────
# ensure_user_row — pure DB tests
# ─────────────────────────────────────────────────────────────────────────


def test_ensure_user_row_inserts_new_user(db_session):
    """A new uid creates a row with default role=2 (FREE)."""
    ensure_user_row("uid-new", "new@example.com", db_session)

    row = db_session.execute(
        text("SELECT user_id, email, role FROM users WHERE user_id='uid-new'")
    ).fetchone()
    assert row is not None
    assert row[0] == "uid-new"
    assert row[1] == "new@example.com"
    assert row[2] == 2  # FREE


def test_ensure_user_row_idempotent(db_session):
    """Calling twice with same uid doesn't raise (UPSERT semantics)."""
    ensure_user_row("uid-dup", "first@example.com", db_session)
    ensure_user_row("uid-dup", "second@example.com", db_session)

    row = db_session.execute(
        text("SELECT email FROM users WHERE user_id='uid-dup'")
    ).fetchone()
    assert row[0] == "second@example.com"


def test_ensure_user_row_no_op_on_empty_uid(db_session):
    """Empty uid = nothing to do (defensive)."""
    ensure_user_row("", "x@y.com", db_session)
    count = db_session.execute(text("SELECT COUNT(*) FROM users")).scalar()
    assert count == 0


def test_ensure_user_row_null_email(db_session):
    """NULL email is allowed (privacy: user may have hidden their email)."""
    ensure_user_row("uid-noemail", None, db_session)

    row = db_session.execute(
        text("SELECT email FROM users WHERE user_id='uid-noemail'")
    ).fetchone()
    assert row[0] is None


def test_ensure_user_row_doesnt_override_existing_role(db_session):
    """Critical: UPSERT must not reset role from ADMIN back to FREE."""
    db_session.execute(text("""
        INSERT INTO users (user_id, email, role)
        VALUES ('uid-admin', 'admin@x.com', 0)
    """))
    db_session.commit()

    ensure_user_row("uid-admin", "admin@x.com", db_session)

    role = db_session.execute(
        text("SELECT role FROM users WHERE user_id='uid-admin'")
    ).scalar()
    assert role == 0


# ─────────────────────────────────────────────────────────────────────────
# get_user_role_from_db — DB lookup
# ─────────────────────────────────────────────────────────────────────────


def test_get_user_role_from_db_admin(db_session):
    """Role 0 in DB maps to UserRole.ADMIN."""
    db_session.execute(text(
        "INSERT INTO users (user_id, role) VALUES ('uid-admin', 0)"
    ))
    db_session.commit()
    clear_role_cache()

    role = get_user_role_from_db("uid-admin", db_session)
    assert role == UserRole.ADMIN


def test_get_user_role_from_db_paid(db_session):
    db_session.execute(text(
        "INSERT INTO users (user_id, role) VALUES ('uid-paid', 1)"
    ))
    db_session.commit()
    clear_role_cache()

    assert get_user_role_from_db("uid-paid", db_session) == UserRole.PAID


def test_get_user_role_from_db_free(db_session):
    db_session.execute(text(
        "INSERT INTO users (user_id, role) VALUES ('uid-free', 2)"
    ))
    db_session.commit()
    clear_role_cache()

    assert get_user_role_from_db("uid-free", db_session) == UserRole.FREE


def test_get_user_role_from_db_missing_user_returns_free(db_session):
    """Defensive: missing user row -> FREE (never 500)."""
    clear_role_cache()
    role = get_user_role_from_db("uid-doesnt-exist", db_session)
    assert role == UserRole.FREE


def test_get_user_role_from_db_empty_uid_returns_free(db_session):
    """Empty uid = FREE (defensive)."""
    clear_role_cache()
    role = get_user_role_from_db("", db_session)
    assert role == UserRole.FREE


def test_get_user_role_from_db_corrupted_role_value(db_session):
    """If DB has role=99 (corrupted), return FREE (fail-safe)."""
    db_session.execute(text(
        "INSERT INTO users (user_id, role) VALUES ('uid-corrupt', 99)"
    ))
    db_session.commit()
    clear_role_cache()

    role = get_user_role_from_db("uid-corrupt", db_session)
    assert role == UserRole.FREE


# ─────────────────────────────────────────────────────────────────────────
# _lookup_role_cached — caching behavior
# ─────────────────────────────────────────────────────────────────────────


def test_lookup_role_cached_returns_correct_enum():
    """Caches int → enum mapping."""
    clear_role_cache()
    assert _lookup_role_cached("uid", 0) == UserRole.ADMIN
    assert _lookup_role_cached("uid", 1) == UserRole.PAID
    assert _lookup_role_cached("uid", 2) == UserRole.FREE


def test_lookup_role_cached_handles_unknown_int():
    """Unknown int falls back to FREE (fail-safe)."""
    clear_role_cache()
    assert _lookup_role_cached("uid", 99) == UserRole.FREE
    assert _lookup_role_cached("uid", -1) == UserRole.FREE


def test_lookup_role_cached_uses_cache_info():
    """Verify caching actually works (cache_info().hits increases)."""
    clear_role_cache()
    _lookup_role_cached("uid-cache", 0)
    info1 = _lookup_role_cached.cache_info()
    assert info1.hits == 0
    assert info1.misses == 1

    _lookup_role_cached("uid-cache", 0)
    info2 = _lookup_role_cached.cache_info()
    assert info2.hits == 1


def test_lookup_role_cached_different_role_db_invalidates():
    """Cache key includes role_db, so role changes invalidate naturally."""
    clear_role_cache()
    _lookup_role_cached("uid-change", 2)  # FREE
    _lookup_role_cached("uid-change", 0)  # ADMIN
    info = _lookup_role_cached.cache_info()
    # Both calls were misses (different cache keys), hits = 0
    assert info.hits == 0
    assert info.misses == 2


def test_clear_role_cache_resets_cache():
    """clear_role_cache() empties the cache."""
    _lookup_role_cached("uid-clear", 0)
    assert _lookup_role_cached.cache_info().currsize > 0
    clear_role_cache()
    assert _lookup_role_cached.cache_info().currsize == 0


# ─────────────────────────────────────────────────────────────────────────
# require_capability — end-to-end via test client
# ─────────────────────────────────────────────────────────────────────────


def test_require_capability_passes_for_admin(db_session):
    """An ADMIN user can access /curate (which requires CURATE_CATALOG)."""
    ensure_user_row("uid-admin", "admin@x.com", db_session)
    db_session.execute(text(
        "UPDATE users SET role=0 WHERE user_id='uid-admin'"
    ))
    db_session.commit()
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-admin", "admin@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/curate", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    assert response.json()["can_curate"] is True


def test_require_capability_blocks_free_user(db_session):
    """A FREE user cannot access /curate."""
    ensure_user_row("uid-free", "free@x.com", db_session)
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-free", "free@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/curate", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403
    assert "curate_catalog" in response.json()["detail"]


def test_require_capability_allows_free_for_basic_view(db_session):
    """FREE user CAN access /view (which requires VIEW_VIDEO)."""
    ensure_user_row("uid-free2", "free2@x.com", db_session)
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-free2", "free2@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/view", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200


def test_require_capability_auto_creates_user_on_first_request(db_session):
    """First request from a new uid auto-creates the user row."""
    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-brand-new", "new@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/view", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200

    row = db_session.execute(text(
        "SELECT role FROM users WHERE user_id='uid-brand-new'"
    )).fetchone()
    assert row is not None
    assert row[0] == 2  # FREE (default)


def test_require_capability_blocks_without_token(db_session):
    """No token = 401 (caught by get_current_user before our check)."""
    test_app = _make_protected_app()
    with TestClient(test_app) as c:
        response = c.get("/curate")
    assert response.status_code == 401


# ─────────────────────────────────────────────────────────────────────────
# require_admin — end-to-end via test client
# ─────────────────────────────────────────────────────────────────────────


def test_require_admin_passes_for_admin(db_session):
    """ADMIN can access /admin-only."""
    ensure_user_row("uid-admin2", "admin2@x.com", db_session)
    db_session.execute(text(
        "UPDATE users SET role=0 WHERE user_id='uid-admin2'"
    ))
    db_session.commit()
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-admin2", "admin2@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    assert response.json()["is_admin"] is True


def test_require_admin_blocks_paid_user(db_session):
    """PAID user (role=1) cannot access /admin-only (admin only)."""
    ensure_user_row("uid-paid", "paid@x.com", db_session)
    db_session.execute(text(
        "UPDATE users SET role=1 WHERE user_id='uid-paid'"
    ))
    db_session.commit()
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-paid", "paid@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403


def test_require_admin_blocks_free_user(db_session):
    """FREE user cannot access /admin-only."""
    ensure_user_row("uid-free3", "free3@x.com", db_session)
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-free3", "free3@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403


def test_require_admin_without_token_returns_401(db_session):
    """No token = 401."""
    test_app = _make_protected_app()
    with TestClient(test_app) as c:
        response = c.get("/admin-only")
    assert response.status_code == 401


# ─────────────────────────────────────────────────────────────────────────
# Cache invalidation end-to-end
# ─────────────────────────────────────────────────────────────────────────


def test_role_change_visible_after_clear_role_cache(db_session):
    """After admin promotes user, clear_role_cache picks up the new role."""
    ensure_user_row("uid-promote", "promote@x.com", db_session)
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-promote", "promote@x.com"),
    ):
        with TestClient(test_app) as c:
            r1 = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert r1.status_code == 403

    # Promote to ADMIN
    db_session.execute(text(
        "UPDATE users SET role=0 WHERE user_id='uid-promote'"
    ))
    db_session.commit()
    clear_role_cache()

    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-promote", "promote@x.com"),
    ):
        with TestClient(test_app) as c:
            r2 = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert r2.status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Security properties
# ─────────────────────────────────────────────────────────────────────────


def test_role_never_trusted_from_claims(db_session):
    """Even if attacker forges Firebase claims with role=0, we ignore it."""
    ensure_user_row("uid-attacker", "attacker@x.com", db_session)
    clear_role_cache()

    # Attacker forges claims claiming they're admin
    forged_claims = _fake_token("uid-attacker", "attacker@x.com")
    forged_claims["role"] = 0          # ← forged
    forged_claims["is_admin"] = True   # ← forged

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=forged_claims,
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    # 403 because role comes from DB (FREE), not claims
    assert response.status_code == 403


def test_unknown_uid_no_user_row_returns_403(db_session):
    """A user that has never logged in can't access admin routes."""
    clear_role_cache()

    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value=_fake_token("uid-ghost", "ghost@x.com"),
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    # ensure_user_row creates FREE row, then 403 fires
    assert response.status_code == 403


# ─────────────────────────────────────────────────────────────────────────
# Edge cases — empty uid, IntegrityError handling
# ─────────────────────────────────────────────────────────────────────────


def test_require_capability_empty_uid_claims_returns_401(db_session):
    """Token with no 'uid' field should return 401, not crash."""
    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"email": "x@y.com"},  # no uid!
    ):
        with TestClient(test_app) as c:
            response = c.get("/curate", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 401


def test_require_admin_empty_uid_claims_returns_401(db_session):
    """Token with no 'uid' field should return 401 for admin routes too."""
    test_app = _make_protected_app()
    with patch(
        "app.auth.dependencies.verify_token",
        return_value={"email": "x@y.com"},  # no uid!
    ):
        with TestClient(test_app) as c:
            response = c.get("/admin-only", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 401


def test_ensure_user_row_swallows_integrity_error(db_session, monkeypatch):
    """If UPSERT somehow raises IntegrityError, we swallow (best-effort).

    Hard to trigger naturally (ON CONFLICT handles all cases), so we
    monkeypatch db.execute to raise IntegrityError once.
    """
    from sqlalchemy.exc import IntegrityError

    real_execute = db_session.execute

    call_count = {"n": 0}

    def fake_execute(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call (the INSERT) raises
            raise IntegrityError("mock", {}, Exception("forced"))
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(db_session, "execute", fake_execute)

    # Should not raise even though the first execute fails
    ensure_user_row("uid-integrity", "x@y.com", db_session)
