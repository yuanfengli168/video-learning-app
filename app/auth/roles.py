"""Roles, capabilities, and the role → capability map.

This module is the SINGLE SOURCE OF TRUTH for "who can do what" in MVP2.

Design overview (see doc/mvp2-roles-and-access.md for full rationale):

1. UserRole (IntEnum, integer column in DB):
   - ADMIN = 0  → full access
   - PAID  = 1  → paid subscriber (v1.1; FREE for v1.0)
   - FREE  = 2  → default tier (read + limited chat)

2. VideoVisibility (IntEnum, integer column on Video):
   - PUBLIC     = 0  → FREE + PAID + ADMIN can see
   - PAID_ONLY  = 1  → PAID + ADMIN (paywall for FREE)
   - ADMIN_ONLY = 2  → ADMIN only (drafts, testing, internal)

3. Capability (str Enum, NOT stored in DB):
   - Granular actions: VIEW_VIDEO, CHAT_FREE, REGEN_MATERIALS, etc.
   - Roles get capabilities via ROLE_CAPABILITIES dict
   - Adding a capability = 1 entry here + 1 line per role = 3-5 lines total

4. ROLE_CAPABILITIES (dict[UserRole, set[Capability]]):
   - The matrix that ties roles to capabilities
   - Adding a new role = 1 enum entry + 1 dict entry = 2 lines

Why integer enums (not string):
- Smaller DB columns (4 bytes vs 16+)
- Faster WHERE clause comparison
- Stable across renames (rename PAID → PREMIUM doesn't break DB)
- Enum gives IDE autocomplete + type checks

Why Capability is a separate concept from UserRole:
- Same role can have different capability subsets in the future
  (e.g. "support_admin" = ADMIN role minus CURATE_CATALOG)
- Easy to grant temporary capabilities (TRIAL = FREE + REGEN_MATERIALS for 14 days)
- Future feature flags can be expressed as capabilities

This module is PURE (no DB, no HTTP, no I/O). It only defines data
structures + helpers. The DB lookup happens in app/auth/admin.py.
"""

from enum import Enum, IntEnum


# ─────────────────────────────────────────────────────────────────────────
# UserRole: identity tier (who you are)
# ─────────────────────────────────────────────────────────────────────────

class UserRole(IntEnum):
    """Identity tier. Stored as integer in users.role column.

    Lower number = more privilege (by convention). Adding new roles:
    append at the end with the next available int. NEVER renumber
    existing values — that breaks every user row in production.
    """

    ADMIN = 0
    """Full access: curate catalog, regenerate materials, manage users."""

    PAID = 1
    """Paid subscriber (v1.1+). Currently no paid tier exists; reserved."""

    FREE = 2
    """Default tier: browse + chat (rate-limited), no upload/curate."""

    # ── Future roles (uncomment when ready, no renumbering) ───────────
    # EDUCATION = 3   # Students/teachers, verified school email
    # TRIAL      = 4   # 14-day trial of paid features
    # BETA       = 5   # Friend-tester tier, higher quota
    # ENTERPRISE = 6   # Corporate accounts, custom limits


# ─────────────────────────────────────────────────────────────────────────
# VideoVisibility: who can see this video (integer on Video.visibility)
# ─────────────────────────────────────────────────────────────────────────

class VideoVisibility(IntEnum):
    """Per-video access tier. Stored as integer in videos.visibility column.

    Query pattern: `WHERE visibility <= max_visibility_for_user_role`.
    For example, FREE users have max=0 (PUBLIC only); PAID users have
    max=1 (PUBLIC + PAID_ONLY); ADMIN has max=2 (everything).

    The "lower is more public" convention lets us use a single <=
    comparison instead of a JOIN or membership check.
    """

    PUBLIC = 0
    """Any signed-in user can see (FREE, PAID, ADMIN)."""

    PAID_ONLY = 1
    """Only PAID + ADMIN can see (paywall for FREE users)."""

    ADMIN_ONLY = 2
    """Only ADMIN can see (drafts, testing, internal-only content)."""


# ─────────────────────────────────────────────────────────────────────────
# Capability: granular actions a user can perform
# ─────────────────────────────────────────────────────────────────────────

class Capability(str, Enum):
    """Granular action a user can perform. NOT stored in DB.

    These are checks like @require_capability(Capability.REGEN_MATERIALS).
    Each role gets a set of capabilities via ROLE_CAPABILITIES below.

    Adding a new capability:
      1. Add the entry here
      2. Add it to ROLE_CAPABILITIES for each role that should have it
      3. Use @require_capability(...) on the relevant endpoint

    Total churn per capability added = 3-5 lines.
    """

    VIEW_VIDEO = "view_video"
    """Browse the catalog and load a video detail page."""

    CHAT_FREE = "chat_free"
    """Send chat messages via the free tier (Groq, rate-limited)."""

    CHAT_PAID = "chat_paid"
    """Send chat messages via the paid tier (Ollama Pro, unlimited)."""

    UPLOAD_VIDEO = "upload_video"
    """Upload a video file (legacy pre-pivot; reserved for v2+)."""

    REGEN_MATERIALS = "regen_materials"
    """Re-run the LLM on a video to regenerate materials (admin/paid)."""

    CURATE_CATALOG = "curate_catalog"
    """Add a new YouTube video to the GLOBAL catalog (admin only)."""

    MANAGE_OWN_COURSE = "manage_own_course"
    """Create/edit one's own courses + sections + upload to them (paid + admin).

    Distinct from CURATE_CATALOG (which adds to the global catalog and
    is admin-only). PAID users can manage their own space but cannot
    add to the curated catalog everyone sees.
    """

    RUN_PLUGIN = "run_plugin"
    """Run a media plugin (e.g. webm_to_mp4 transcoding) on a video.

    Admin-only because plugins spawn ffmpeg subprocesses with
    significant CPU + disk cost. Day 9 hotfix: was previously
    open to anyone signed in (security gap, would let free users
    DoS the server by queuing transcodes).
    """

    MANAGE_USERS = "manage_users"
    """Change user roles, view user list, audit log (admin only)."""

    VIEW_ADMIN_DASHBOARD = "view_admin_dashboard"
    """Access /admin/* pages + the events dashboard (admin only)."""


# ─────────────────────────────────────────────────────────────────────────
# ROLE_CAPABILITIES: the matrix that ties roles to capabilities
# ─────────────────────────────────────────────────────────────────────────

# Each role maps to a SET of capabilities. frozenset so it's hashable +
# immutable (accidental mutation would be a silent security bug).

ROLE_CAPABILITIES: dict[UserRole, frozenset[Capability]] = {
    UserRole.ADMIN: frozenset({
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
        Capability.CHAT_PAID,
        Capability.UPLOAD_VIDEO,
        Capability.REGEN_MATERIALS,
        Capability.CURATE_CATALOG,
        Capability.MANAGE_OWN_COURSE,
        Capability.RUN_PLUGIN,
        Capability.MANAGE_USERS,
        Capability.VIEW_ADMIN_DASHBOARD,
    }),

    UserRole.PAID: frozenset({
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
        Capability.CHAT_PAID,
        Capability.UPLOAD_VIDEO,
        Capability.REGEN_MATERIALS,
        # Day 9 hotfix: PAID can manage own courses + sections
        Capability.MANAGE_OWN_COURSE,
        # No CURATE_CATALOG, no MANAGE_USERS, no admin dashboard, no RUN_PLUGIN
    }),

    UserRole.FREE: frozenset({
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
        # No upload, no regen, no curate, no manage_own, no run_plugin
    }),
}


# ─────────────────────────────────────────────────────────────────────────
# Helpers (pure functions, no I/O)
# ─────────────────────────────────────────────────────────────────────────

def user_has_capability(role: UserRole | int | None, capability: Capability) -> bool:
    """True if the role grants this capability.

    Accepts both UserRole enum and raw int (from DB) for ergonomics.
    None or unknown role = no capabilities (defense in depth).
    """
    if role is None:
        return False
    # Accept int or enum transparently
    if isinstance(role, int) and not isinstance(role, UserRole):
        try:
            role = UserRole(role)
        except ValueError:
            # Unknown int value (e.g. role=99 from corrupted DB)
            return False
    return capability in ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_for_role(role: UserRole | int | None) -> frozenset[Capability]:
    """Return all capabilities for a role.

    Used by UI to render admin-only links (hide what user can't access).
    Returns empty frozenset for None / unknown role (defense in depth).
    """
    if role is None:
        return frozenset()
    if isinstance(role, int) and not isinstance(role, UserRole):
        try:
            role = UserRole(role)
        except ValueError:
            return frozenset()
    return ROLE_CAPABILITIES.get(role, frozenset())


def max_visibility_for_role(role: UserRole | int | None) -> VideoVisibility:
    """Return the highest visibility tier this role can see.

    Used in catalog queries: `WHERE visibility <= max_visibility_for_role(user)`.
    FREE → PUBLIC only (0)
    PAID → PUBLIC + PAID_ONLY (1)
    ADMIN → PUBLIC + PAID_ONLY + ADMIN_ONLY (2)

    Returns ADMIN_ONLY for unknown roles (most restrictive, fail-safe).
    """
    if role is None:
        return VideoVisibility.PUBLIC  # safest default for unauthenticated
    if isinstance(role, int) and not isinstance(role, UserRole):
        try:
            role = UserRole(role)
        except ValueError:
            return VideoVisibility.PUBLIC
    return {
        UserRole.ADMIN: VideoVisibility.ADMIN_ONLY,
        UserRole.PAID: VideoVisibility.PAID_ONLY,
        UserRole.FREE: VideoVisibility.PUBLIC,
    }.get(role, VideoVisibility.PUBLIC)


def role_name(role: UserRole | int | None) -> str:
    """Return string name for JSON serialization.

    We do NOT expose the int value in API responses — clients shouldn't
    know that ADMIN=0. Return "admin" / "paid" / "free" / "unknown".
    """
    if role is None:
        return "unknown"
    if isinstance(role, int) and not isinstance(role, UserRole):
        try:
            role = UserRole(role)
        except ValueError:
            return "unknown"
    return {
        UserRole.ADMIN: "admin",
        UserRole.PAID: "paid",
        UserRole.FREE: "free",
    }.get(role, "unknown")


def visibility_name(visibility: VideoVisibility | int | None) -> str:
    """Return string name for JSON serialization of visibility."""
    if visibility is None:
        return "unknown"
    if isinstance(visibility, int) and not isinstance(visibility, VideoVisibility):
        try:
            visibility = VideoVisibility(visibility)
        except ValueError:
            return "unknown"
    return {
        VideoVisibility.PUBLIC: "public",
        VideoVisibility.PAID_ONLY: "paid_only",
        VideoVisibility.ADMIN_ONLY: "admin_only",
    }.get(visibility, "unknown")


def user_can_access_video(
    user_role: "UserRole | int | None",
    video_visibility: "VideoVisibility | int | None",
) -> bool:
    """Return True if a user with the given role can access a video of this visibility.

    Day 5 hotfix: pre-Day-1 the access check was `course.user_id == user.uid`
    (only the uploader could see their video). With admin-curated videos
    (Day 2A onwards) the correct check is visibility-tier + role:

      - PUBLIC video: anyone (including signed-out) can access
      - PAID_ONLY: PAID + ADMIN roles
      - ADMIN_ONLY: ADMIN role only

    Usage:
        if not user_can_access_video(user.get("role"), video.visibility):
            raise HTTPException(status_code=403, detail="Not authorized for this video")

    Admins (UserRole.ADMIN, value 0) implicitly access everything.
    Unknown roles are denied (fail-safe — defaults to PUBLIC visibility).
    """
    if user_role is None:
        return max_visibility_for_role(None) >= VideoVisibility(video_visibility or 0)
    try:
        if isinstance(user_role, int) and not isinstance(user_role, UserRole):
            user_role = UserRole(user_role)
    except ValueError:
        return False
    return max_visibility_for_role(user_role) >= VideoVisibility(video_visibility or 0)
