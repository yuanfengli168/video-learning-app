"""Tests for app/auth/roles.py — role/visibility/capability helpers.

Day 5 hotfix: user_can_access_video() is the new visibility-based
replacement for the old "course.user_id == uid" ownership check.
"""

import pytest

from app.auth.roles import (
    UserRole,
    VideoVisibility,
    user_can_access_video,
)


# ─────────────────────────────────────────────────────────────────────────
# user_can_access_video — the helper that fixes the Day 2A/2C oversight
# ─────────────────────────────────────────────────────────────────────────


class TestUserCanAccessVideo:
    """Visibility-tier matrix: rows = user role, cols = video visibility."""

    # ADMIN (role=0) can see everything
    @pytest.mark.parametrize("visibility", [0, 1, 2])
    def test_admin_can_see_everything(self, visibility):
        assert user_can_access_video(UserRole.ADMIN, visibility) is True

    # PAID (role=1) can see PUBLIC + PAID_ONLY, NOT ADMIN_ONLY
    def test_paid_can_see_public(self):
        assert user_can_access_video(UserRole.PAID, 0) is True

    def test_paid_can_see_paid_only(self):
        assert user_can_access_video(UserRole.PAID, 1) is True

    def test_paid_cannot_see_admin_only(self):
        assert user_can_access_video(UserRole.PAID, 2) is False

    # FREE (role=2) can only see PUBLIC
    def test_free_can_see_public(self):
        assert user_can_access_video(UserRole.FREE, 0) is True

    def test_free_cannot_see_paid_only(self):
        assert user_can_access_video(UserRole.FREE, 1) is False

    def test_free_cannot_see_admin_only(self):
        assert user_can_access_video(UserRole.FREE, 2) is False

    # Anonymous (no role) behaves like FREE (safest default)
    def test_anonymous_can_see_public(self):
        assert user_can_access_video(None, 0) is True

    def test_anonymous_cannot_see_paid_only(self):
        assert user_can_access_video(None, 1) is False

    def test_anonymous_cannot_see_admin_only(self):
        assert user_can_access_video(None, 2) is False

    # Accepts both int and enum forms (callers pass user.get('role') which is int)
    def test_accepts_int_role(self):
        # role=0 (ADMIN) should access visibility=1 (PAID_ONLY)
        assert user_can_access_video(0, 1) is True
        # role=2 (FREE) should be blocked from visibility=1
        assert user_can_access_video(2, 1) is False

    # Accepts VideoVisibility enum too
    def test_accepts_visibility_enum(self):
        assert user_can_access_video(UserRole.PAID, VideoVisibility.PUBLIC) is True
        assert user_can_access_video(UserRole.PAID, VideoVisibility.ADMIN_ONLY) is False

    # Fail-safe: unknown int role value → deny (UserRole(99) raises ValueError)
    def test_unknown_int_role_denied(self):
        # role=99 is an int that's not a valid UserRole; the helper
        # fails closed (returns False). The value never reaches a route
        # in practice (ensure_user_row normalizes to 0/1/2) but defense.
        assert user_can_access_video(99, 0) is False

    # Fail-safe: garbage role TYPE (str) → max_visibility_for_role returns PUBLIC
    def test_garbage_role_type_treated_as_public(self):
        # A non-int, non-UserRole value (e.g. from a corrupted session)
        # shouldn't crash; it should be treated as PUBLIC (safest default
        # per app/auth/roles.py:max_visibility_for_role).
        assert user_can_access_video("not-a-role", 0) is True

    # None visibility treated as PUBLIC (no field → 0)
    def test_none_visibility_treated_as_public(self):
        assert user_can_access_video(UserRole.FREE, None) is True
