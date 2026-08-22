"""Tests for app/auth/roles.py — pure-logic role/capability helpers.

This module has NO I/O so tests run instantly (no DB, no HTTP). Coverage
target: 100% (every branch of every helper function).

Coverage:
- UserRole enum values + ordering
- VideoVisibility enum values + ordering
- Capability enum values + uniqueness
- ROLE_CAPABILITIES shape (frozen, correct membership per role)
- user_has_capability() — enum + int + None + unknown int
- capabilities_for_role() — enum + int + None + unknown int
- max_visibility_for_role() — all 3 roles + edge cases
- role_name() + visibility_name() — all values + unknown + None
"""

import pytest

from app.auth.roles import (
    Capability,
    ROLE_CAPABILITIES,
    UserRole,
    VideoVisibility,
    capabilities_for_role,
    max_visibility_for_role,
    role_name,
    user_has_capability,
    visibility_name,
)


# ── Enum basics ──────────────────────────────────────────────────────────


def test_userrole_has_expected_values():
    """UserRole values match the design (0, 1, 2 = ADMIN, PAID, FREE)."""
    assert UserRole.ADMIN == 0
    assert UserRole.PAID == 1
    assert UserRole.FREE == 2
    assert len(list(UserRole)) == 3


def test_userrole_is_intenum():
    """UserRole members compare as ints (DB-friendly)."""
    assert UserRole.ADMIN < UserRole.PAID < UserRole.FREE
    assert int(UserRole.ADMIN) == 0
    # Can pass directly to DB query: WHERE role = :role
    assert UserRole.ADMIN.value == 0


def test_videovisibility_has_expected_values():
    """Visibility values: lower = more public."""
    assert VideoVisibility.PUBLIC == 0
    assert VideoVisibility.PAID_ONLY == 1
    assert VideoVisibility.ADMIN_ONLY == 2
    assert len(list(VideoVisibility)) == 3


def test_capability_values_are_unique_strings():
    """Each capability has a unique lowercase-with-underscores string."""
    values = [c.value for c in Capability]
    assert len(values) == len(set(values))  # no duplicates
    # Spot-check format
    assert Capability.VIEW_VIDEO.value == "view_video"
    assert Capability.CURATE_CATALOG.value == "curate_catalog"


# ── ROLE_CAPABILITIES shape ──────────────────────────────────────────────


def test_role_capabilities_covers_all_roles():
    """Every UserRole has an entry (no missing roles)."""
    assert set(ROLE_CAPABILITIES.keys()) == set(UserRole)


def test_role_capabilities_values_are_frozensets():
    """Frozen sets prevent accidental mutation (security property)."""
    for caps in ROLE_CAPABILITIES.values():
        assert isinstance(caps, frozenset)


def test_admin_has_all_capabilities():
    """ADMIN role gets every defined capability."""
    admin_caps = ROLE_CAPABILITIES[UserRole.ADMIN]
    assert admin_caps == frozenset(Capability)


def test_paid_lacks_admin_only_capabilities():
    """PAID has no CURATE_CATALOG / MANAGE_USERS / VIEW_ADMIN_DASHBOARD."""
    paid_caps = ROLE_CAPABILITIES[UserRole.PAID]
    assert Capability.CURATE_CATALOG not in paid_caps
    assert Capability.MANAGE_USERS not in paid_caps
    assert Capability.VIEW_ADMIN_DASHBOARD not in paid_caps


def test_paid_has_paid_features():
    """PAID gets REGEN_MATERIALS + CHAT_PAID + UPLOAD_VIDEO."""
    paid_caps = ROLE_CAPABILITIES[UserRole.PAID]
    assert Capability.REGEN_MATERIALS in paid_caps
    assert Capability.CHAT_PAID in paid_caps
    assert Capability.UPLOAD_VIDEO in paid_caps


def test_free_only_gets_read_and_chat():
    """FREE has only VIEW_VIDEO + CHAT_FREE, nothing else."""
    free_caps = ROLE_CAPABILITIES[UserRole.FREE]
    assert free_caps == frozenset({
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
    })


def test_free_cannot_curate_regen_upload_manage():
    """Explicit negative assertions for clarity."""
    free_caps = ROLE_CAPABILITIES[UserRole.FREE]
    for forbidden in (
        Capability.CURATE_CATALOG,
        Capability.REGEN_MATERIALS,
        Capability.UPLOAD_VIDEO,
        Capability.MANAGE_USERS,
        Capability.CHAT_PAID,
        Capability.VIEW_ADMIN_DASHBOARD,
    ):
        assert forbidden not in free_caps, (
            f"FREE must NOT have {forbidden.value}"
        )


# ── user_has_capability() ───────────────────────────────────────────────


def test_user_has_capability_admin_true():
    """ADMIN has every capability."""
    for cap in Capability:
        assert user_has_capability(UserRole.ADMIN, cap) is True


def test_user_has_capability_free_cannot_curate():
    """FREE cannot curate (the key gate)."""
    assert user_has_capability(UserRole.FREE, Capability.CURATE_CATALOG) is False


def test_user_has_capability_accepts_int():
    """Helper accepts raw int (from DB column)."""
    assert user_has_capability(0, Capability.CURATE_CATALOG) is True
    assert user_has_capability(2, Capability.CURATE_CATALOG) is False
    assert user_has_capability(1, Capability.REGEN_MATERIALS) is True


def test_user_has_capability_accepts_none():
    """None role = no capabilities (fail-safe)."""
    assert user_has_capability(None, Capability.VIEW_VIDEO) is False


def test_user_has_capability_unknown_int():
    """Unknown int (e.g. from corrupted DB) = no capabilities (fail-safe)."""
    assert user_has_capability(99, Capability.VIEW_VIDEO) is False
    assert user_has_capability(-1, Capability.VIEW_VIDEO) is False


def test_user_has_capability_invalid_int_string():
    """String passed instead of int doesn't crash."""
    assert user_has_capability("admin", Capability.VIEW_VIDEO) is False


# ── capabilities_for_role() ─────────────────────────────────────────────


def test_capabilities_for_role_returns_frozenset():
    """Returns immutable set (UI consumers can't mutate it)."""
    caps = capabilities_for_role(UserRole.ADMIN)
    assert isinstance(caps, frozenset)


def test_capabilities_for_role_admin_full():
    """ADMIN gets all 8 capabilities."""
    caps = capabilities_for_role(UserRole.ADMIN)
    assert len(caps) == len(Capability)


def test_capabilities_for_role_paid_subset_of_admin():
    """PAID caps are a strict subset of ADMIN caps."""
    paid = capabilities_for_role(UserRole.PAID)
    admin = capabilities_for_role(UserRole.ADMIN)
    assert paid < admin  # strict subset


def test_capabilities_for_role_free_subset_of_paid():
    """FREE caps are a strict subset of PAID caps."""
    free = capabilities_for_role(UserRole.FREE)
    paid = capabilities_for_role(UserRole.PAID)
    assert free < paid


def test_capabilities_for_role_none_empty():
    """None returns empty frozenset (fail-safe)."""
    assert capabilities_for_role(None) == frozenset()


def test_capabilities_for_role_unknown_int_empty():
    """Unknown int returns empty (fail-safe)."""
    assert capabilities_for_role(42) == frozenset()


def test_capabilities_for_role_accepts_int():
    """Accepts raw int just like user_has_capability."""
    free_caps = capabilities_for_role(2)
    assert free_caps == frozenset({
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
    })


# ── max_visibility_for_role() ────────────────────────────────────────────


def test_max_visibility_admin_sees_everything():
    """ADMIN can see ADMIN_ONLY videos."""
    assert max_visibility_for_role(UserRole.ADMIN) == VideoVisibility.ADMIN_ONLY


def test_max_visibility_paid_sees_paid_only():
    """PAID can see PUBLIC + PAID_ONLY but not ADMIN_ONLY."""
    assert max_visibility_for_role(UserRole.PAID) == VideoVisibility.PAID_ONLY


def test_max_visibility_free_only_public():
    """FREE can see only PUBLIC videos."""
    assert max_visibility_for_role(UserRole.FREE) == VideoVisibility.PUBLIC


def test_max_visibility_none_falls_back_to_public():
    """None = safest default (PUBLIC), not most-restrictive.

    Rationale: None likely means 'unauthenticated' request (shouldn't
    happen since all endpoints require auth, but defense in depth).
    Returning PUBLIC means the query filter excludes everything > 0,
    which is the safest behavior.
    """
    assert max_visibility_for_role(None) == VideoVisibility.PUBLIC


def test_max_visibility_unknown_int_falls_back_to_public():
    """Unknown int → fail-safe to PUBLIC."""
    assert max_visibility_for_role(99) == VideoVisibility.PUBLIC


def test_max_visibility_accepts_int():
    """Accepts raw int (for DB queries that pass role col directly)."""
    assert max_visibility_for_role(0) == VideoVisibility.ADMIN_ONLY
    assert max_visibility_for_role(2) == VideoVisibility.PUBLIC


# ── role_name() — JSON serialization helper ─────────────────────────────


def test_role_name_admin():
    assert role_name(UserRole.ADMIN) == "admin"


def test_role_name_paid():
    assert role_name(UserRole.PAID) == "paid"


def test_role_name_free():
    assert role_name(UserRole.FREE) == "free"


def test_role_name_int_values():
    """Accepts int (from DB), never exposes the int itself."""
    assert role_name(0) == "admin"
    assert role_name(1) == "paid"
    assert role_name(2) == "free"


def test_role_name_unknown_int():
    """Unknown int value → 'unknown' string (never crash)."""
    assert role_name(99) == "unknown"
    assert role_name(-1) == "unknown"


def test_role_name_none():
    """None → 'unknown'."""
    assert role_name(None) == "unknown"


def test_role_name_string_input():
    """String passed (e.g. role='admin') → 'unknown' (we don't guess)."""
    # We only accept enum or int; strings would be a programmer error
    # in the calling code, but the helper shouldn't crash.
    assert role_name("admin") == "unknown"


# ── visibility_name() — JSON serialization helper ───────────────────────


def test_visibility_name_public():
    assert visibility_name(VideoVisibility.PUBLIC) == "public"


def test_visibility_name_paid_only():
    assert visibility_name(VideoVisibility.PAID_ONLY) == "paid_only"


def test_visibility_name_admin_only():
    assert visibility_name(VideoVisibility.ADMIN_ONLY) == "admin_only"


def test_visibility_name_int_values():
    """Accepts int (from DB)."""
    assert visibility_name(0) == "public"
    assert visibility_name(1) == "paid_only"
    assert visibility_name(2) == "admin_only"


def test_visibility_name_unknown_int():
    """Unknown int → 'unknown' (fail-safe)."""
    assert visibility_name(99) == "unknown"


def test_visibility_name_none():
    """None → 'unknown'."""
    assert visibility_name(None) == "unknown"


# ── Property: adding a future role is a 5-line change ───────────────────


def test_capabilities_map_is_dict_so_easy_to_extend():
    """Sanity: ROLE_CAPABILITIES is a plain dict (easy to add entries).

    This is more of a design property test — it documents the contract
    that 'add a new role' means 'add 1 enum entry + 1 dict entry'.
    """
    assert isinstance(ROLE_CAPABILITIES, dict)
    # Adding EDUCATION would be: ROLE_CAPABILITIES[UserRole.EDUCATION] = frozenset({...})
    # This test ensures dict access pattern works.
    assert ROLE_CAPABILITIES[UserRole.ADMIN]


# ── Property: visibility ordering enables <= query ──────────────────────


def test_visibility_ordering_supports_simple_query():
    """The 'lower is more public' convention lets us use WHERE visibility <= N.

    This is a design-property test: it documents the contract that the
    visibility enum values are intentionally ordered so that one
    <= comparison handles the catalog filter for all 3 roles.
    """
    # If FREE has max_visibility=PUBLIC (0), then:
    #   WHERE videos.visibility <= 0  →  only PUBLIC videos
    free_max = max_visibility_for_role(UserRole.FREE).value
    paid_max = max_visibility_for_role(UserRole.PAID).value
    admin_max = max_visibility_for_role(UserRole.ADMIN).value

    assert free_max < paid_max < admin_max
    # Specifically:
    assert free_max == 0
    assert paid_max == 1
    assert admin_max == 2
