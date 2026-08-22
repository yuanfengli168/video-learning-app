# MVP2 Roles & Access Control

> **Status**: Design finalized; implementation on Day 2 of 14-day build.
> **Last updated**: 2026-08-22
> **Related**: `mvp2-final-go-live-plan.md`, `mvp2-llm-architecture.md`

This doc captures the role/visibility system: who can do what, how roles
are stored, how promotion works, and how content visibility ties in.

---

## 1. UserRole enum (integer, not string)

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

### Promote yourself (admin = jackyli)

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
VALUES ('YOUR_FIREBASE_UID', 'jackyopenclaw.168@gmail.com', 0)
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
INSERT OR IGNORE INTO paid_waitlist (email, message, source)
VALUES ('jackyopenclaw.168@gmail.com',
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

## 8. Migration plan (v1.0 → v1.1)

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
  paid waitlist (bootstrap with jackyopenclaw.168@gmail.com), admin UI
  visibility choice, self-promotion attack analysis, v1.0→v1.1 migration
  plan. Implementation on Day 2 of 14-day build.
