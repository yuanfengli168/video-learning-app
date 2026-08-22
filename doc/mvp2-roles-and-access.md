# MVP2 Roles & Access Control

> **Status**: Design finalized; implementation on Day 2 of 14-day build.
> **Last updated**: 2026-08-22
> **Related**: `mvp2-final-go-live-plan.md`, `mvp2-llm-architecture.md`

This doc captures the role/visibility system: who can do what, how roles
are stored, how promotion works, how content visibility ties in, and how
the paywall UX flows.

---

## 0. Future-proofing the enum (extensibility)

**Anticipated future roles**: EDUCATION (students/teachers), TRIAL (14-day
free trial of paid features), BETA (friend-testers with extra quota),
ENTERPRISE (corporate accounts). We design now so adding these is a
**5-line change**, not a refactor.

### Design: Hybrid (IntEnum role + Capability map)

```python
# app/auth/roles.py

from enum import IntEnum
from enum import Enum


class UserRole(IntEnum):
    """Identity tier. Lower number = more privilege (by convention)."""
    ADMIN = 0          # Internal, full access
    PAID = 1           # Subscribed user
    FREE = 2           # Default tier
    # Future (add in v1.1+ without breaking existing rows):
    # EDUCATION = 3     # Students/teachers (school email verified)
    # TRIAL = 4         # 14-day trial of paid features
    # BETA = 5          # Friend-testers, higher quota
    # ENTERPRISE = 6    # Corporate accounts, custom limits


class Capability(str, Enum):
    """Granular actions a user can perform.

    Adding a new capability = one new entry here + one role-capability map
    line. NO router code changes required (the @require_capability decorator
    reads from the map at request time).
    """
    VIEW_VIDEO = "view_video"
    CHAT_FREE = "chat_free"                  # 30 RPD via Groq
    CHAT_PAID = "chat_paid"                  # unlimited via Ollama Pro/local
    UPLOAD_VIDEO = "upload_video"            # (legacy pre-pivot)
    REGEN_MATERIALS = "regen_materials"      # admin re-runs LLM on video
    CURATE_CATALOG = "curate_catalog"        # admin adds YouTube videos
    MANAGE_USERS = "manage_users"            # change roles, view list
    VIEW_ADMIN_DASHBOARD = "view_admin_dashboard"


# Map: which capabilities does each role have?
ROLE_CAPABILITIES: dict[UserRole, set[Capability]] = {
    UserRole.ADMIN: {
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE, Capability.CHAT_PAID,
        Capability.UPLOAD_VIDEO,
        Capability.REGEN_MATERIALS,
        Capability.CURATE_CATALOG,
        Capability.MANAGE_USERS,
        Capability.VIEW_ADMIN_DASHBOARD,
    },
    UserRole.PAID: {
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE, Capability.CHAT_PAID,
        Capability.UPLOAD_VIDEO,
        Capability.REGEN_MATERIALS,
        # No CURATE_CATALOG, no MANAGE_USERS, no admin dashboard
    },
    UserRole.FREE: {
        Capability.VIEW_VIDEO,
        Capability.CHAT_FREE,
        # No upload, no regen, no curate, no manage
    },
}


def user_has_capability(role: UserRole, capability: Capability) -> bool:
    """Single check used by @require_capability decorator."""
    return capability in ROLE_CAPABILITIES.get(role, set())


def capabilities_for(role: UserRole) -> set[Capability]:
    """Return all capabilities for a role (for UI rendering)."""
    return ROLE_CAPABILITIES.get(role, set())
```

### Adding a new role (e.g. EDUCATION) — 5 lines

```python
# 1. Add to enum
EDUCATION = 3

# 2. Add to capabilities map
ROLE_CAPABILITIES[UserRole.EDUCATION] = {
    Capability.VIEW_VIDEO,
    Capability.CHAT_FREE,
    Capability.REGEN_MATERIALS,   # students can regen study materials
    # No upload, curate, or manage_users
}

# Done. Router code, frontend checks, DB queries — all unchanged.
```

### Adding a new capability (e.g. BOOKMARKS) — 3 lines

```python
# 1. Add to enum
BOOKMARK_VIDEO = "bookmark_video"

# 2. Add to each role that should have it
ROLE_CAPABILITIES[UserRole.PAID].add(Capability.BOOKMARK_VIDEO)
ROLE_CAPABILITIES[UserRole.FREE].add(Capability.BOOKMARK_VIDEO)
# (Admin gets it automatically if you want — add to ADMIN set too)

# Done.
```

### Router usage

```python
# app/routers/admin.py

from functools import wraps
from app.auth.roles import Capability, user_has_capability

def require_capability(capability: Capability):
    """Decorator: 403 if current user's role doesn't include this capability."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user = kwargs.get('user') or args[-1]  # depends on FastAPI signature
            role = get_user_role(db, user['uid'])
            if not user_has_capability(role, capability):
                raise HTTPException(403, f"Missing capability: {capability.value}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator


@router.post("/api/admin/videos/youtube")
@require_capability(Capability.CURATE_CATALOG)
async def admin_add_youtube_video(...):
    ...
```

### Why not just check `role == UserRole.ADMIN`?

- Hard to add new admin-tier roles later (e.g., "support_admin" that can
  manage users but can't curate)
- Capability checks are reusable (one decorator, many endpoints)
- Easy to grant temporary capabilities (trials, promotions)
- Future "feature flags" can grant/revoke capabilities per-user

---

Why integer (not string like `"admin"` / `"paid"` / `"free"`):
- Smaller in DB (4 bytes vs 16+ bytes)
- Faster comparison (`WHERE role = 0` vs `WHERE role = 'admin'`)
- Stable across renames (rename "PAID" to "PREMIUM" without DB migration)
- Enum gives us type safety + autocomplete

```python
# app/auth/roles.py (NEW)

from enum import IntEnum


class UserRole(IntEnum):
    """User access tier. Lower number = more privilege.

    Why IntEnum (not str Enum):
    - Integer columns are faster + smaller
    - Stable across rename (PAID → PREMIUM doesn't break DB)
    - Enum gives IDE autocomplete + type checks
    """
    ADMIN = 0   # Full access: curate catalog, regenerate materials, manage users
    PAID = 1    # (v1.1) Subscribed: full chat, regen materials, premium content
    FREE = 2    # Read-only: browse free videos, chat (rate-limited), no upload


# Convenience constants
IS_ADMIN = "is_admin"
IS_PAID = "is_paid"
IS_FREE = "is_free"
```

### Capability matrix

| Capability | ADMIN (0) | PAID (1) | FREE (2) |
|---|---|---|---|
| Browse catalog | ✅ | ✅ | ✅ (PUBLIC only) |
| Watch videos | ✅ | ✅ | ✅ (PUBLIC only) |
| Chat about videos | ✅ unlimited | ✅ unlimited | ✅ 30 RPD |
| View admin-curated materials (summary/mindmap/quiz) | ✅ | ✅ | ✅ |
| Add YouTube video (curate catalog) | ✅ | ❌ | ❌ |
| Re-generate materials | ✅ | ✅ | ❌ |
| Upload own video (legacy, pre-pivot) | ✅ | ✅ (rate-limited) | ❌ |
| Convert WebM → MP4 (legacy) | ✅ | ✅ | ❌ |
| Edit any user's content | ✅ | ❌ | � |
| View admin dashboard / events | ✅ | ❌ | ❌ |
| Manage users / roles | ✅ (manual SQL only v1.0) | ❌ | ❌ |
| Access paid-only content | ✅ | ✅ | ❌ (paywall) |

---

## 2. VideoVisibility enum

Why a separate enum for content visibility (not just role-based):
- Same role can have access to different content sets
- Admin can curate a "Premium tier" without giving users admin powers
- Paywall logic is data-driven, not code-driven

```python
# app/auth/visibility.py (NEW)

from enum import IntEnum


class VideoVisibility(IntEnum):
    """Who can see this video.

    ADMIN_ONLY is for internal/scratch/admin-curation drafts that
    should never leak to users. PUBLIC is the default.
    """
    PUBLIC = 0      # Anyone (FREE, PAID, ADMIN) can see
    PAID_ONLY = 1   # Only PAID + ADMIN (paywall for FREE)
    ADMIN_ONLY = 2  # Only admin (drafts, testing, internal)
```

### Visibility logic in router

```python
# app/routers/videos.py

@router.get("/api/videos")
async def list_videos(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    role = get_user_role(db, user["uid"])  # cached lookup
    max_visibility = {
        UserRole.ADMIN: VideoVisibility.ADMIN_ONLY,
        UserRole.PAID: VideoVisibility.PAID_ONLY,
        UserRole.FREE: VideoVisibility.PUBLIC,
    }[role]

    videos = db.query(Video).filter(
        Video.visibility <= max_visibility
    ).order_by(Video.created_at.desc()).all()
    return videos
```

**For FREE users on PAID_ONLY videos**: server returns 404 (or 200 with `locked=True` flag). UI shows paywall screen.

---

## 3. Users table schema

```sql
-- app/database.py:_MIGRATIONS new entry

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,         -- matches Firebase UID
    email TEXT,                       -- Firebase email (nullable for privacy)
    role INTEGER NOT NULL DEFAULT 2,  -- UserRole enum, default FREE
    paid_interest_at DATETIME,        -- (v1.1) when user opted into paid waitlist
    notes TEXT,                       -- admin notes (e.g. "early tester")
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_email ON users(email);
```

### Auto-creation on first login

```python
# app/auth/dependencies.py:get_current_user

async def get_current_user(...):
    claims = verify_token(token)
    uid = claims["uid"]
    email = claims.get("email", "")

    # Auto-create users row on first authenticated request
    db.execute(
        text("""
            INSERT INTO users (user_id, email, role)
            VALUES (:uid, :email, 2)
            ON CONFLICT(user_id) DO UPDATE SET
                email = COALESCE(excluded.email, users.email)
        """),
        {"uid": uid, "email": email},
    )
    db.commit()

    return {"uid": uid, "email": email, **claims}
```

### Role cache for performance

Don't query DB on every page load. Cache role in the session cookie or in-process LRU:

```python
# app/auth/roles.py

from functools import lru_cache

@lru_cache(maxsize=10000)
def get_user_role(uid: str, role_db_lookup: int = -1) -> UserRole:
    """Cached role lookup. role_db_lookup is the actual DB value; we use it
    as part of the cache key so cache invalidates when role changes."""
    return UserRole(role_db_lookup)
```

For admin role changes (manual SQL UPDATE), invalidate by calling
`get_user_role.cache_clear()` or by restarting the process. For v1.0, just
restart-on-deploy is fine.

---

## 4. Manual admin promotion (v1.0)

**No admin UI for v1.0.** Admin promotion is manual SQLite INSERT/UPDATE.

### Promote yourself (admin = jacky.li)

Your personal admin email is **your-personal-email@example.com** (forever account).

```bash
cd /Users/jackyli/Desktop/Githubs/video-learning-app

# 1. Find your Firebase UID by logging in to the web app and checking
#    browser devtools → Application → Local Storage → look for Firebase user
#    Or run:
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
    "SELECT user_id, email, role FROM users;"

# 2. Promote (replace 'YOUR_FIREBASE_UID' with your actual UID)
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db <<'EOF'
INSERT INTO users (user_id, email, role)
VALUES ('YOUR_FIREBASE_UID', 'your-personal-email@example.com', 0)
ON CONFLICT(user_id) DO UPDATE SET role = 0, updated_at = CURRENT_TIMESTAMP;
EOF

# 3. Verify
sqlite3 /Volumes/Storage-Fast-NVMe/video_learning.db \
    "SELECT user_id, email, role FROM users WHERE role = 0;"
```

### Demote (back to FREE)

```sql
UPDATE users SET role = 2, updated_at = CURRENT_TIMESTAMP
WHERE user_id = 'some-uid';
```

### SECURITY: NEVER accept role from user input

- Firebase token is verified, but its `claims` are user-controlled (anyone
  can edit their Firebase profile claims)
- We **never trust** the Firebase claim for role
- We **always look up** role from our DB (the only source of truth)
- No API endpoint accepts role as input — admin role is mutated ONLY via
  direct SQLite access (admin must have shell access to the server)

This means:
- No "promote me to admin" form
- No "I'm a paid user, trust me" request header
- No SQL injection vector (no user input ever reaches a role query)
- Admin promotion requires SSH/shell access = already trusted

---

## 5. Paid-user waitlist (v1.1, but capture emails now)

People interested in the paid tier (when payments go live) leave their email.
For v1.0, we collect these emails with a simple form, stored in a new table.

```sql
CREATE TABLE IF NOT EXISTS paid_waitlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    message TEXT,                   -- optional: why they want paid
    source TEXT DEFAULT 'web',      -- where signup came from
    notified_at DATETIME,           -- when we emailed them about launch
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Bootstrap email (already exists)

```sql
-- Pre-load admin's own email so we don't forget it later
-- (This is jacky.li's forever personal account; will be the first paid
-- user when payments go live in v1.1)
INSERT OR IGNORE INTO paid_waitlist (email, message, source)
VALUES ('your-personal-email@example.com',
        'Founder/admin — bootstrap entry',
        'manual');
```

### UI surface (v1.0)

Add a small "Want unlimited chats + premium videos? Leave your email" form
on the home page. Posts to `/api/waitlist/paid` which:
1. Validates email format (basic regex, no PII beyond email)
2. INSERT OR IGNORE into `paid_waitlist`
3. Returns 200 (no email confirmation in v1.0 — that's a v1.1 polish)

**Note**: this is a marketing signal, not a payment commitment. No charge
happens until v1.1 ships payments.

---

## 6. Adding videos: visibility choice (admin UI)

### Admin-only form on `/admin/videos/new`

```
┌────────────────────────────────────────────────┐
│  Add a YouTube video                            │
│                                                  │
│  YouTube URL: [___________________________]      │
│  Title:       [___________________________]      │
│  Description: [___________________________]      │
│                                                  │
│  Visibility:                                     │
│    ○ Public       (FREE, PAID, ADMIN can see)     │
│    ○ Paid only    (PAID, ADMIN can see)           │
│    ○ Admin only   (ADMIN can see — drafts)        │
│                                                  │
│  [Add Video]                                     │
└────────────────────────────────────────────────┘
```

Default: **Public** (most common case — admin curates great content for all).

### Backend logic

```python
# app/routers/admin.py

@router.post("/api/admin/videos/youtube")
async def admin_add_youtube_video(
    body: YouTubeVideoCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(require_admin),
):
    video_id = extract_youtube_id(body.url)
    video = Video(
        title=body.title,
        youtube_id=video_id,
        visibility=body.visibility,  # int from VideoVisibility enum
        status="pending",
        # ... other fields
    )
    db.add(video)
    db.commit()
    queue_transcribe_job(video.id)
    return {"video_id": video.id, "youtube_id": video_id}
```

---

## 7. Self-promotion attack analysis

**Threat**: User tricks the system into granting themselves admin.

### Attack surface review

| Attack | Blocked by |
|---|---|
| Forge Firebase UID claim `role=admin` | We never read role from Firebase; we look up DB |
| POST `/api/admin/videos/youtube` with valid user token | `require_admin` checks DB role, returns 403 |
| SQL injection: `' OR role=0 --` | All queries use SQLAlchemy ORM or parameterized text() — no string concat |
| Manipulate session cookie to claim admin | Session cookie is signed JWT, can't be forged without secret |
| Call internal admin endpoint directly | All admin endpoints require `require_admin` dep |
| Brute force Firebase UID | Firebase rate limits + our role check would still say FREE |
| Edit browser localStorage to change role | Role is server-side DB, not client-side |

### Defense in depth

1. **Server-side role check** (the only source of truth)
2. **Parameterized SQL everywhere** (no string interpolation in queries)
3. **Admin endpoints require explicit `require_admin` dep** (not hidden in middleware)
4. **No client-side role gating** (UI hides admin links for FREE, but server enforces regardless)
5. **Manual SQLite for promotion** (no API path to grant admin)
6. **Audit log**: every admin action logged to `events` table with uid + timestamp

If you want extra paranoia in v1.1: add a separate `admin_users` table that's
immutable from the API (admin role comes from this table, never from the
`users.role` column that any future bug might expose). For v1.0, the manual
SQLite approach + the defenses above are sufficient.

---

## 8. Paywall UX (v1.1)

When a FREE user encounters a PAID_ONLY video:

### Catalog page (`/api/videos` response)

For PAID_ONLY/ADMIN_ONLY videos, the catalog includes:
```json
{
  "id": "abc123",
  "title": "Advanced Vector DB Internals",
  "youtube_id": "XYZ",
  "thumbnail_url": "https://...",
  "visibility": 1,                    // PAID_ONLY
  "locked": true,                     // user can't watch
  "lock_reason": "premium_content",
  "preview_description": "...",       // first 200 chars of admin-curated description
  "price_hint": "Subscribe to unlock" // marketing text
}
```

### Video detail page (`/video/<id>`)

If user tries to load a locked video:
- Server returns 200 with `locked: true` (NOT 404, so UI can show explanation)
- Frontend renders **Paywall component** instead of the YouTube iframe:
  - Big 🔒 icon
  - "Premium content"
  - 1-sentence teaser ("Master the inner workings of HNSW indexing...")
  - **"Subscribe to unlock"** button → email waitlist form (or Stripe checkout in v1.1)
  - Small text: "Already subscribed? [Log in with a different account](#)"

### Hover tooltip (catalog cards)

On the video card, hovering over a locked thumbnail shows:
```
🔒 Premium content
Subscribe to watch
```

### Admin view (no lock)

For admins, ALL videos show unlocked regardless of visibility (admin can
preview what paid users see). This is critical for content curation.

### Implementation

```python
# app/routers/videos.py:get_video

@router.get("/api/videos/{video_id}")
async def get_video(video_id: str, ...):
    video = db.get(Video, video_id)
    role = get_user_role(db, user['uid'])

    # Admin sees everything
    if role == UserRole.ADMIN:
        return video.to_dict(include_youtube_id=True)

    # User's max visible tier
    max_visibility = {PAID: PAID_ONLY, FREE: PUBLIC}[role]

    if video.visibility > max_visibility:
        # Locked: return metadata but hide the YouTube embed URL
        d = video.to_dict(include_youtube_id=False)
        d['locked'] = True
        d['lock_reason'] = 'premium_content' if video.visibility == PAID_ONLY \
                          else 'admin_only'
        return d

    return video.to_dict(include_youtube_id=True)
```

```python
# app/models/video.py — to_dict method

def to_dict(self, include_youtube_id: bool = True) -> dict:
    d = {
        "id": self.id,
        "title": self.title,
        "thumbnail_url": f"https://i.ytimg.com/vi/{self.youtube_id}/hqdefault.jpg",
        "duration": self.duration,
        "visibility": self.visibility,
    }
    if include_youtube_id:
        d["youtube_id"] = self.youtube_id
    return d
```

**Key**: never embed YouTube iframe for locked videos. The youtube_id stays
hidden until user has access, preventing workarounds.

---

## 9. Migration plan (v1.0 → v1.1)

When payments go live:

1. **Add Stripe SDK**, webhook handler (`/api/stripe/webhook`)
2. **Add `subscriptions` table** (stripe_customer_id, stripe_subscription_id,
   status, current_period_end, plan)
3. **Auto-promote on successful subscription**: webhook → `UPDATE users SET
   role = 1 WHERE user_id = ?`
4. **Auto-demote on subscription end**: cron daily checks
   `current_period_end < now()` → demote to FREE
5. **Email waitlist** when payments go live: `SELECT email FROM
   paid_waitlist WHERE notified_at IS NULL` → send via SendGrid/Resend
6. **Admin UI for user management** (replace manual SQLite):
   - `/admin/users` → list users, change roles, view subscriptions
   - Audit log for all role changes

---

## Update log

- **2026-08-22** — Created doc capturing UserRole enum (0=ADMIN, 1=PAID,
  2=FREE), VideoVisibility enum (PUBLIC, PAID_ONLY, ADMIN_ONLY),
  capabilities matrix, users table schema, manual promotion via SQLite,
  paid waitlist (bootstrap with your-personal-email@example.com — admin's
  forever personal account), admin UI visibility choice, self-promotion
  attack analysis, v1.0→v1.1 migration plan.

  **Updated 2026-08-22** — Added Section 0 (Capability-based hybrid design
  for future extensibility: UserRole stays int, Capability is a separate
  enum, ROLE_CAPABILITIES map controls what each role can do; adding
  EDUCATION/TRIAL/BETA roles = 5-line change), Section 8 (Paywall UX for
  FREE users on PAID_ONLY videos: locked metadata returned, YouTube ID
  hidden, hover tooltip "🔒 Premium content", paywall landing page on
  video detail), updated admin email to your-personal-email@example.com.
  Implementation on Day 2 of 14-day build.
